from __future__ import annotations
import os
import tempfile
import threading
from flask import Flask, request, jsonify, send_file, render_template, abort

from core.models import Dictionary, Entry
from core.wiki_client import WikiClient
from core.crawler import manager as crawl_manager
from exporters.stardict_exporter import export_stardict, export_syn_only
from exporters.dictd_exporter import export_dict
from exporters.simple_exporters import export_csv, export_txt, export_json
from exporters.koreader_exporter import zip_stardict_folder
from importers.importer import import_any

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64MB upload cap

WORKDIR = tempfile.mkdtemp(prefix="hieroglyphs_")
os.makedirs(WORKDIR, exist_ok=True)

# --- single-user in-memory "current project" ---------------------------
_project_lock = threading.Lock()
current_dictionary = Dictionary(name="Untitled Dictionary")


def _entry_summary(e: Entry) -> dict:
    return {
        "id": e.id,
        "headword": e.headword,
        "body": e.body,
        "aliases": e.aliases,
        "redirect_to": e.redirect_to,
        "see_more": e.see_more,
        "source_page": e.source_page,
        "source_url": e.source_url,
    }


@app.get("/")
def index():
    return render_template("index.html")


# --- feature 11: page counter as soon as a base URL is entered ---------
@app.post("/api/count_pages")
def count_pages():
    data = request.get_json(force=True)
    base_url = (data.get("base_url") or "").strip()
    if not base_url:
        return jsonify({"error": "base_url required"}), 400
    try:
        client = WikiClient(base_url)
        count = client.count_pages()
        return jsonify({"page_count": count, "base_url": client.base_url})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400


# --- crawl lifecycle -----------------------------------------------------
@app.post("/api/crawl/start")
def crawl_start():
    data = request.get_json(force=True)
    base_url = (data.get("base_url") or "").strip()
    dict_name = (data.get("dict_name") or "My Fandom Dictionary").strip()
    apply_tense_fix = bool(data.get("apply_tense_fix", True))
    if not base_url:
        return jsonify({"error": "base_url required"}), 400
    job = crawl_manager.create(base_url, dict_name, apply_tense_fix)
    return jsonify({"job_id": job.job_id})


@app.get("/api/crawl/status/<job_id>")
def crawl_status(job_id):
    job = crawl_manager.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job.to_dict())


@app.post("/api/crawl/commit/<job_id>")
def crawl_commit(job_id):
    """Adopt a finished (or manually stopped) crawl job's result as the
    current project - a stopped crawl's partial dictionary is still fully
    valid to use."""
    global current_dictionary
    job = crawl_manager.get(job_id)
    if not job or job.status not in ("done", "stopped") or not job.dictionary:
        return jsonify({"error": "job not finished"}), 400
    with _project_lock:
        current_dictionary = job.dictionary
    return jsonify({"ok": True, "entry_count": len(current_dictionary.entries)})


@app.post("/api/crawl/pause/<job_id>")
def crawl_pause(job_id):
    job = crawl_manager.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    job.pause()
    return jsonify({"ok": True})


@app.post("/api/crawl/resume/<job_id>")
def crawl_resume(job_id):
    job = crawl_manager.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    job.resume()
    return jsonify({"ok": True})


@app.post("/api/crawl/stop/<job_id>")
def crawl_stop(job_id):
    job = crawl_manager.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    job.stop()
    return jsonify({"ok": True})


# --- project: browse / search / edit -------------------------------------
@app.get("/api/project")
def get_project():
    with _project_lock:
        alias_count = sum(len(e.aliases) for e in current_dictionary.entries)
        return jsonify({
            "name": current_dictionary.name,
            "base_url": current_dictionary.base_url,
            "entry_count": len(current_dictionary.entries),
            "alias_count": alias_count,
        })


@app.get("/api/project/random")
def random_entry():
    """Quality-of-life: jump to a random real entry for browsing/discovery."""
    import random
    with _project_lock:
        real_entries = [e for e in current_dictionary.entries if not e.redirect_to and e.body.strip()]
        if not real_entries:
            return jsonify({"error": "no entries"}), 404
        return jsonify(_entry_summary(random.choice(real_entries)))


def _resolve(entry: Entry, by_headword: dict, depth: int = 0) -> tuple[Entry, str | None]:
    """
    Follow an entry's redirect_to chain (if any) down to a real, defined
    entry. Returns (final_entry, redirected_from_headword_or_None).
    Guards against redirect loops with a depth cap.
    """
    original = entry.headword
    seen = {entry.headword.lower()}
    current = entry
    while current.redirect_to and depth < 8:
        target = by_headword.get(current.redirect_to.lower())
        if not target or target.headword.lower() in seen:
            break
        seen.add(target.headword.lower())
        current = target
        depth += 1
    redirected_from = original if current is not entry else None
    return current, redirected_from


@app.get("/api/project/entries")
def list_entries():
    """
    Search results always resolve redirects/aliases to the real entry
    they point at (feature 10) - a redirect never shows up as its own
    bare "see also" stub card, it transparently becomes the target entry,
    exactly like looking the word up directly. Results are sorted
    alphabetically for easier browsing.
    """
    q = (request.args.get("q") or "").strip().lower()
    limit = int(request.args.get("limit", 200))
    with _project_lock:
        by_headword = {e.headword.lower(): e for e in current_dictionary.entries}
        entries = current_dictionary.entries
        if q:
            def matches(e: Entry):
                if q in e.headword.lower():
                    return True
                if any(q in a.lower() for a in e.aliases):
                    return True
                return False
            entries = [e for e in entries if matches(e)]

        resolved = []
        seen_ids = set()
        for e in sorted(entries, key=lambda e: e.headword.lower()):
            final, redirected_from = _resolve(e, by_headword)
            if final.id in seen_ids:
                continue
            seen_ids.add(final.id)
            summary = _entry_summary(final)
            if redirected_from:
                summary["redirected_from"] = redirected_from
            resolved.append(summary)
        return jsonify(resolved[:limit])


@app.get("/api/project/lookup")
def lookup_entry():
    """Feature 10: exact-ish headword/alias lookup with redirect resolution,
    e.g. 'Roque' -> the 'Roque au Fabii' entry, 'Mustang' -> 'Virginia au
    Augustus'. Always resolves straight through to the real, defined
    entry - never returns a bare redirect stub.

    Under normal use each alias is only ever owned by one entry (the
    crawler's confidence-based resolution guarantees that), so this is
    just a straightforward lookup. The fallback below only matters for
    hand-edited or imported dictionaries where a duplicate alias might
    have been added manually."""
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify({"error": "q required"}), 400
    with _project_lock:
        by_headword = {e.headword.lower(): e for e in current_dictionary.entries}
        match = by_headword.get(q)
        if not match:
            match = next((e for e in current_dictionary.entries if q in [a.lower() for a in e.aliases]), None)
        if not match:
            return jsonify({"error": "not found"}), 404
        final, redirected_from = _resolve(match, by_headword)
        summary = _entry_summary(final)
        summary["query"] = (request.args.get("q") or "").strip()
        if redirected_from:
            summary["redirected_from"] = redirected_from
        return jsonify(summary)


@app.post("/api/project/entry")
def upsert_entry():
    data = request.get_json(force=True)
    with _project_lock:
        entry_id = data.get("id")
        existing = next((e for e in current_dictionary.entries if e.id == entry_id), None) if entry_id else None
        if existing:
            existing.headword = data.get("headword", existing.headword)
            existing.body = data.get("body", existing.body)
            existing.aliases = data.get("aliases", existing.aliases)
            existing.redirect_to = data.get("redirect_to", existing.redirect_to)
            return jsonify(_entry_summary(existing))
        new_entry = Entry(
            headword=data.get("headword", "").strip(),
            body=data.get("body", ""),
            aliases=data.get("aliases", []) or [],
            redirect_to=data.get("redirect_to") or None,
            source_page=data.get("source_page", ""),
            source_url=data.get("source_url", ""),
        )
        current_dictionary.add(new_entry)
        return jsonify(_entry_summary(new_entry))


@app.delete("/api/project/entry/<entry_id>")
def delete_entry(entry_id):
    with _project_lock:
        before = len(current_dictionary.entries)
        current_dictionary.entries = [e for e in current_dictionary.entries if e.id != entry_id]
        return jsonify({"deleted": before - len(current_dictionary.entries)})


@app.post("/api/project/new")
def new_project():
    global current_dictionary
    data = request.get_json(force=True)
    with _project_lock:
        current_dictionary = Dictionary(name=(data.get("name") or "Untitled Dictionary").strip())
    return jsonify({"ok": True})


@app.post("/api/project/rename")
def rename_project():
    data = request.get_json(force=True)
    with _project_lock:
        current_dictionary.name = (data.get("name") or current_dictionary.name).strip()
    return jsonify({"ok": True, "name": current_dictionary.name})


# --- feature 9: import dictionary files -----------------------------------
@app.post("/api/project/import")
def import_project():
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    f = request.files["file"]
    mode = request.args.get("mode", "merge")  # merge | replace
    tmp_path = os.path.join(WORKDIR, f.filename)
    f.save(tmp_path)
    try:
        imported = import_any(tmp_path)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400

    global current_dictionary
    with _project_lock:
        if mode == "replace" or len(current_dictionary.entries) == 0:
            current_dictionary = imported
        else:
            existing_headwords = {e.headword.lower() for e in current_dictionary.entries}
            for e in imported.entries:
                if e.headword.lower() not in existing_headwords:
                    current_dictionary.add(e)
        return jsonify({"ok": True, "imported_count": len(imported.entries),
                         "total_entries": len(current_dictionary.entries)})


# --- feature 4/5/12: exports ------------------------------------------------
@app.get("/api/project/export/<fmt>")
def export_project(fmt):
    with _project_lock:
        dictionary = current_dictionary
        base_name = "".join(c if c.isalnum() else "_" for c in dictionary.name).strip("_") or "dictionary"

        if fmt == "json":
            path = os.path.join(WORKDIR, f"{base_name}.json")
            export_json(dictionary, path)
            return send_file(path, as_attachment=True, download_name=f"{base_name}.json")

        if fmt == "csv":
            path = os.path.join(WORKDIR, f"{base_name}.csv")
            export_csv(dictionary, path)
            return send_file(path, as_attachment=True, download_name=f"{base_name}.csv")

        if fmt == "txt":
            path = os.path.join(WORKDIR, f"{base_name}.txt")
            export_txt(dictionary, path)
            return send_file(path, as_attachment=True, download_name=f"{base_name}.txt")

        if fmt == "dict":
            dict_path, index_path = export_dict(dictionary, WORKDIR, base_name)
            zip_no_ext = os.path.join(WORKDIR, f"{base_name}_dictd")
            import shutil
            bundle_dir = os.path.join(WORKDIR, f"{base_name}_dictd_bundle")
            os.makedirs(bundle_dir, exist_ok=True)
            shutil.copy(dict_path, bundle_dir)
            shutil.copy(index_path, bundle_dir)
            archive = shutil.make_archive(zip_no_ext, "zip", root_dir=WORKDIR,
                                           base_dir=os.path.basename(bundle_dir))
            return send_file(archive, as_attachment=True, download_name=f"{base_name}_dict.zip")

        if fmt in ("stardict", "koreader"):
            stardict_dir = export_stardict(dictionary, WORKDIR)
            zip_no_ext = os.path.join(WORKDIR, f"{base_name}_stardict")
            archive = zip_stardict_folder(stardict_dir, zip_no_ext)
            suffix = "koreader" if fmt == "koreader" else "stardict"
            return send_file(archive, as_attachment=True, download_name=f"{base_name}_{suffix}.zip")

        if fmt == "syn":
            path = os.path.join(WORKDIR, f"{base_name}.syn.txt")
            export_syn_only(dictionary, path)
            return send_file(path, as_attachment=True, download_name=f"{base_name}.syn.txt")

        abort(404)


@app.get("/api/project/export_all")
def export_all():
    """Quality-of-life: every export format bundled into one zip, so you
    don't have to click through each format separately."""
    import shutil
    with _project_lock:
        dictionary = current_dictionary
        base_name = "".join(c if c.isalnum() else "_" for c in dictionary.name).strip("_") or "dictionary"
        bundle_dir = os.path.join(WORKDIR, f"{base_name}_all_formats")
        os.makedirs(bundle_dir, exist_ok=True)

        export_json(dictionary, os.path.join(bundle_dir, f"{base_name}.json"))
        export_csv(dictionary, os.path.join(bundle_dir, f"{base_name}.csv"))
        export_txt(dictionary, os.path.join(bundle_dir, f"{base_name}.txt"))
        export_syn_only(dictionary, os.path.join(bundle_dir, f"{base_name}.syn.txt"))
        dict_path, index_path = export_dict(dictionary, bundle_dir, base_name)
        stardict_dir = export_stardict(dictionary, bundle_dir)

        zip_no_ext = os.path.join(WORKDIR, f"{base_name}_all_formats")
        archive = shutil.make_archive(zip_no_ext, "zip", root_dir=WORKDIR,
                                       base_dir=os.path.basename(bundle_dir))
        return send_file(archive, as_attachment=True, download_name=f"{base_name}_all_formats.zip")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    print(f"Hieroglyphs running at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
