"""
automationTesting.py - tests every route of the My Notes app over HTTP.

Same idea as testAutomation.py from the L44-L45 lecture (Requests + a list of
test cases + assert), extended to a real CRUD app backed by MongoDB:
GET and POST, redirects, HTML pages, JSON, validation errors and 404s.

Usage:
    python automationTesting.py                       # http://localhost:3000
    BASE_URL=http://localhost:3050 python automationTesting.py
    python automationTesting.py http://localhost:3050

Optional: WAIT_SECONDS=60 waits for /health before starting (default 30).

Every check prints PASS or FAIL. The script exits with status 1 if anything
failed, so GitHub Actions stops the pipeline before the image is pushed.
Notes it creates are deleted again at the end.
"""

import os
import sys
import time
import uuid

import requests

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BASE_URL", "http://localhost:3000")).rstrip("/")
WAIT_SECONDS = int(os.environ.get("WAIT_SECONDS", "30"))
MISSING_ID = "000000000000000000000000"  # valid ObjectId format, but no such note

# A unique marker so this run's notes never clash with existing data
RUN = uuid.uuid4().hex[:8]
TITLE = f"CI note {RUN}"
EDITED_TITLE = f"CI note {RUN} (edited)"
CATEGORY = f"ci-{RUN}"

session = requests.Session()
results = []  # (passed, name, detail)
created_ids = []


def url(path):
    return BASE_URL + path


def check(name, condition, detail=""):
    results.append((bool(condition), name, detail))
    status = "PASS" if condition else "FAIL"
    line = f"{status}  {name}"
    if not condition and detail:
        line += f"  ->  {detail}"
    print(line, flush=True)
    return bool(condition)


def get(path, **kwargs):
    return session.get(url(path), allow_redirects=False, timeout=10, **kwargs)


def post(path, data=None):
    return session.post(url(path), data=data or {}, allow_redirects=False, timeout=10)


def api_notes():
    return get("/api/notes").json()["notes"]


def find_note(title):
    return next((n for n in api_notes() if n["title"] == title), None)


def wait_for_health():
    deadline = time.time() + WAIT_SECONDS
    while True:
        try:
            if session.get(url("/health"), timeout=3).status_code == 200:
                return True
        except requests.RequestException:
            pass
        if time.time() > deadline:
            return False
        time.sleep(1)


# --------------------------------------------------------------------------- #
# Test cases, in the order a user would hit them
# --------------------------------------------------------------------------- #

def test_health():
    r = get("/health")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check("GET /health returns 200", r.status_code == 200, f"got {r.status_code}")
    check("GET /health reports status ok and database up",
          body.get("status") == "ok" and body.get("database") == "up", str(body))


def test_home_redirect():
    r = get("/")
    check("GET / redirects to /notes",
          r.status_code in (301, 302) and r.headers.get("location") == "/notes",
          f"got {r.status_code} location={r.headers.get('location')}")


def test_list_page():
    r = get("/notes")
    check("GET /notes returns 200 HTML", r.status_code == 200 and "text/html" in r.headers.get("content-type", ""),
          f"got {r.status_code} {r.headers.get('content-type')}")
    check("GET /notes renders the notes page", "All notes" in r.text and "Search by title" in r.text)


def test_new_form():
    r = get("/notes/new")
    check("GET /notes/new returns 200", r.status_code == 200, f"got {r.status_code}")
    check("GET /notes/new has the add form",
          'action="/notes"' in r.text and 'name="title"' in r.text and 'name="content"' in r.text)


def test_create():
    r = post("/notes", {"title": TITLE, "content": "Created by automationTesting.py", "category": CATEGORY})
    check("POST /notes (valid) redirects to /notes",
          r.status_code == 302 and r.headers.get("location") == "/notes",
          f"got {r.status_code} location={r.headers.get('location')}")
    note = find_note(TITLE)
    if note:
        created_ids.append(note["_id"])
    check("POST /notes stored the note in MongoDB", note is not None and note["category"] == CATEGORY, str(note))
    check("new note appears on GET /notes", TITLE in get("/notes").text)
    return note


def test_validation():
    cases = [
        ({"title": "", "content": "no title", "category": "General"}, "Title is required.", "empty title"),
        ({"title": "no content", "content": "", "category": "General"}, "Content is required.", "empty content"),
        ({"title": "x" * 121, "content": "too long", "category": "General"}, "120 characters or fewer", "title over 120 chars"),
        ({"title": "   ", "content": "   ", "category": ""}, "Title is required.", "whitespace-only fields"),
    ]
    for data, message, label in cases:
        r = post("/notes", data)
        check(f"POST /notes ({label}) returns 422", r.status_code == 422, f"got {r.status_code}")
        check(f"POST /notes ({label}) shows the error message", message in r.text)

    # the form keeps what the user typed
    r = post("/notes", {"title": "", "content": f"keep me {RUN}", "category": "Work"})
    check("422 response keeps the submitted content", f"keep me {RUN}" in r.text)
    check("invalid notes are not saved", not any(f"keep me {RUN}" in n["content"] for n in api_notes()))


def test_search(note):
    r = get("/notes", params={"q": RUN})
    check("GET /notes?q=<word> returns 200", r.status_code == 200, f"got {r.status_code}")
    check("search by title finds the note", TITLE in r.text)

    r = get("/notes", params={"q": RUN.upper()})
    check("search is case-insensitive", TITLE in r.text)

    r = get("/notes", params={"q": f"zz-no-match-{RUN}"})
    check("search with no match shows the empty message", r.status_code == 200 and "No notes match this search" in r.text)

    r = get("/notes", params={"q": ".*"})
    check("regex characters are searched literally (q=.*)", r.status_code == 200 and TITLE not in r.text)


def test_category_filter():
    r = get("/notes", params={"category": CATEGORY})
    check("GET /notes?category=<name> returns 200", r.status_code == 200, f"got {r.status_code}")
    check("category filter shows the note", TITLE in r.text)
    rendered = r.text.count('class="note"')
    check("category filter shows only that category", rendered == 1, f"{rendered} notes rendered")
    check("new category appears in the dropdown", f'value="{CATEGORY}"' in get("/notes").text)

    r = get("/notes", params={"category": "Study", "q": RUN})
    check("search + category combined excludes the note", TITLE not in r.text)


def test_edit(note):
    note_id = note["_id"]
    r = get(f"/notes/{note_id}/edit")
    check("GET /notes/:id/edit returns 200", r.status_code == 200, f"got {r.status_code}")
    check("edit form is pre-filled with the note", TITLE in r.text and f'action="/notes/{note_id}"' in r.text)

    r = post(f"/notes/{note_id}", {"title": EDITED_TITLE, "content": "Updated by automationTesting.py", "category": CATEGORY})
    check("POST /notes/:id (update) redirects to /notes", r.status_code == 302, f"got {r.status_code}")
    updated = find_note(EDITED_TITLE)
    check("update saved the new title and set updatedAt",
          updated is not None and updated["_id"] == note_id and "updatedAt" in updated, str(updated))
    check("old title is gone after update", find_note(TITLE) is None)

    r = post(f"/notes/{note_id}", {"title": "", "content": "", "category": CATEGORY})
    check("POST /notes/:id with invalid data returns 422", r.status_code == 422, f"got {r.status_code}")
    check("invalid update did not change the note", find_note(EDITED_TITLE) is not None)


def test_not_found():
    for path in ["/notes/not-a-real-id/edit", f"/notes/{MISSING_ID}/edit", "/this-page-does-not-exist"]:
        r = get(path)
        check(f"GET {path} returns 404", r.status_code == 404, f"got {r.status_code}")

    for path in ["/notes/not-a-real-id", f"/notes/{MISSING_ID}"]:
        r = post(path, {"title": "x", "content": "y", "category": "General"})
        check(f"POST {path} (update) returns 404", r.status_code == 404, f"got {r.status_code}")

    for path in ["/notes/not-a-real-id/delete", f"/notes/{MISSING_ID}/delete"]:
        r = post(path)
        check(f"POST {path} returns 404", r.status_code == 404, f"got {r.status_code}")

    r = get("/notes/not-a-real-id/edit")
    check("404 page is the app's error page, not a stack trace", "That page or note does not exist." in r.text)


def test_api():
    r = get("/api/notes")
    check("GET /api/notes returns 200 JSON",
          r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"),
          f"got {r.status_code} {r.headers.get('content-type')}")
    body = r.json()
    check("GET /api/notes has database, collection, count and notes",
          all(k in body for k in ("database", "collection", "count", "notes")), str(list(body)))
    check("GET /api/notes count matches the notes list", body["count"] == len(body["notes"]))
    check("GET /api/notes uses database notes_lab", body["database"] == "notes_lab", body["database"])


def test_db_page():
    r = get("/db")
    check("GET /db returns 200", r.status_code == 200, f"got {r.status_code}")
    check("GET /db shows the database inspector", "Database Inspector" in r.text and "notes_lab" in r.text)


def test_static():
    r = get("/css/style.css")
    check("GET /css/style.css is served as CSS",
          r.status_code == 200 and "text/css" in r.headers.get("content-type", ""), f"got {r.status_code}")


def test_delete(note):
    note_id = note["_id"]
    r = post(f"/notes/{note_id}/delete")
    check("POST /notes/:id/delete redirects to /notes", r.status_code == 302, f"got {r.status_code}")
    check("deleted note is gone from MongoDB", all(n["_id"] != note_id for n in api_notes()))
    r = post(f"/notes/{note_id}/delete")
    check("deleting the same note again returns 404", r.status_code == 404, f"got {r.status_code}")
    if note_id in created_ids:
        created_ids.remove(note_id)


def cleanup():
    for note_id in list(created_ids):
        try:
            post(f"/notes/{note_id}/delete")
        except requests.RequestException:
            pass


def test():
    print(f"Testing {BASE_URL}  (run {RUN})")
    if not wait_for_health():
        check(f"server is reachable at {BASE_URL}/health", False, f"no 200 within {WAIT_SECONDS}s")
        return

    try:
        test_health()
        test_home_redirect()
        test_list_page()
        test_new_form()
        note = test_create()
        test_validation()
        if note:
            test_search(note)
            test_category_filter()
            test_edit(note)
        test_not_found()
        test_api()
        test_db_page()
        test_static()
        if note:
            test_delete(note)
    except Exception as exc:  # a crash in a test is a failure, not a pass
        check("test run finished without an exception", False, repr(exc))
    finally:
        cleanup()


if __name__ == "__main__":
    test()
    failed = [r for r in results if not r[0]]
    print("-" * 60)
    print(f"{len(results) - len(failed)} passed, {len(failed)} failed, {len(results)} total")
    if failed:
        print("FAILED:")
        for _, name, detail in failed:
            print(f"  - {name}  {detail}")
        sys.exit(1)
    print("All tests passed!")
