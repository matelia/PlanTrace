r"""
Step 10: Contacts, and drafts asking utility owners for what's missing.

Reads what the earlier steps found and the documents themselves:
  - 811 DESIGN TICKETS (UDig NY style PDFs): the ticket number, the design
    area, and every member utility with its phone and contact person.
  - PRINTED EMAILS among the plans: who sent the records, and when. Replies
    to automatic ticket systems reach nobody; those are flagged.
  - EACH SHEET'S RESULTS: placement status, your review verdict, dates,
    scale, datum. From those it works out what to ask each owner for:
      * sheets that couldn't be placed (or you marked wrong): a GIS/CAD file
        or a copy with coordinates / ties to the street centerlines
      * GIS screenshots: the GIS data itself
      * sheets with no date: the date of record or last revision
      * owners whose sheets never state a datum: their coordinate system
      * owners on the ticket who sent nothing: their records

Results, in projects\<PROJECT_NAME>\requests\  (stays on this computer):
  contacts.csv        every contact found, with the file and page it came from
  summary.md          what was received from each owner and what's missing
  draft_<owner>.txt   one email/phone-call draft per owner to ask

NOTHING IS SENT. The drafts are starting points: check every line, add your
signature, and send them yourself if you decide to.

    python scripts\10_contacts_and_requests.py
"""

import csv
import glob
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from plantrace import records

import pypdf

OUT_DIR = os.path.join(config.PROJECT_DIR, "requests")
FOOTPRINTS_FC = os.path.join(config.GDB_PATH, "PlanFootprints")
REVIEW_BACKUP = os.path.join(config.PROJECT_DIR, "review_backup.csv")
PLACED_OK = ("from PDF", "from coordinates", "good")


def load_manifest():
    with open(config.MANIFEST_PATH, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def pdf_texts(path):
    """Text layer of each page ('' for scanned pages)."""
    try:
        return [(p.extract_text() or "") for p in pypdf.PdfReader(path).pages]
    except Exception as e:
        print("   could not read text from", os.path.basename(path), "-", e)
        return []


def sheet_needs(row, review):
    """What to ask about one plan sheet: a list of request keys."""
    needs = []
    status = row.get("place_status") or ""
    kind = row.get("sheet_kind") or ""
    verdict = review[0] if review else ""
    if status == "photo map":
        needs.append("gis_data")
    elif kind == "unknown" or verdict == "wrong" or status in ("poor", ""):
        # Couldn't be placed, or you marked it wrong. ("check" sheets you
        # haven't reviewed yet aren't asked about: review them first.)
        needs.append("position")
    if status not in ("from PDF",) and not records.years_in(row.get("dates_found")):
        needs.append("date")
    placed = status in PLACED_OK or verdict in ("correct", "close")
    if not placed and status != "photo map" and not row.get("scale_found"):
        needs.append("scale")
    return needs


REQUEST_TEXT = {
    "position": ("We could not confirm where these sheets sit on the ground. A GIS or CAD file "
                 "of your facilities in the design area (shapefile, file geodatabase, DWG or DGN) "
                 "would be best; otherwise a copy that shows coordinates, or ties/dimensions to "
                 "street centerlines or property lines."),
    "gis_data": ("These look like screen captures of your GIS map. Could you send the GIS data "
                 "itself for the design area (shapefile, file geodatabase, or a GeoPDF)?"),
    "date": "What is the date of record (or last revision) of these sheets?",
    "scale": "What scale are these sheets drawn at? We could not find one written on them.",
}


def nice_name(member):
    """'ADAMS COUNTY WATER AUTHORITY' -> 'Adams County Water Authority'
    (short words such as MCI, NYS, DOT stay in capitals)."""
    return " ".join(w if len(w) <= 3 and w.isalpha() else w.title() for w in member.split())


def short(pid):
    for text in config.SHORT_NAME_REMOVE:
        pid = pid.replace(text, "")
    return pid


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = load_manifest()
    reviews = records.read_reviews(FOOTPRINTS_FC, REVIEW_BACKUP)

    # --- 1. Read every PDF's text layer: tickets and emails ----------------
    tickets, emails = [], []
    for pdf in sorted(glob.glob(os.path.join(config.RAW_PLANS_DIR, "*.pdf"))):
        texts = pdf_texts(pdf)
        ticket = records.read_ticket(texts)
        if ticket:
            ticket["file"] = os.path.basename(pdf)
            tickets.append(ticket)
            continue
        for page_no, text in enumerate(texts, 1):
            mail = records.read_email_page(text)
            if mail:
                mail.update(file=os.path.basename(pdf), page=page_no)
                emails.append(mail)
    print("Found {} design ticket(s) and {} printed email page(s).".format(len(tickets), len(emails)))

    # --- 2. Contacts, grouped by owner --------------------------------------
    contacts = []          # one row per (owner, contact, source)
    owner_contact = {}     # owner -> the ticket member row to address the draft to
    ticket_members = {}    # member name -> owner name (or the member's own name)
    for t in tickets:
        for m in t["members"]:
            owner = nice_name(m["member"])
            for key, name, ticket_text in config.UTILITY_OWNERS:
                if ticket_text and ticket_text.upper() in m["member"].upper():
                    owner = name
                    break
            ticket_members[m["member"]] = owner
            entry = owner_contact.setdefault(owner, dict(m, tickets=[]))
            entry["tickets"].append(t["number"])
            contacts.append({
                "owner": owner, "organisation": m["member"], "contact": m["contact"],
                "phone": m["phone"], "email": "", "utility_types": m["utility_types"],
                "role": "811 ticket member contact",
                "source": "{} p{}".format(t["file"], m["page"]), "ticket": t["number"]})
        req = t["requester"]
        if req["name"]:
            contacts.append({
                "owner": "(ticket requester)", "organisation": req["company"], "contact": req["name"].title(),
                "phone": req["phone"], "email": req["email"], "utility_types": "",
                "role": "filed the design ticket", "source": t["file"] + " p1", "ticket": t["number"]})
    for mail in emails:
        owner, _ = records.owner_of(mail["file"], config.UTILITY_OWNERS)
        contacts.append({
            "owner": owner, "organisation": "", "contact": mail["from"], "phone": "",
            "email": mail["email"], "utility_types": "",
            "role": "sent records by email" + (" (automatic system: replies reach nobody)" if mail["automated"] else ""),
            "source": "{} p{} ({})".format(mail["file"], mail["page"], mail["sent"]), "ticket": ""})

    with open(os.path.join(OUT_DIR, "contacts.csv"), "w", newline="", encoding="utf-8") as f:
        cols = ["owner", "organisation", "contact", "role", "phone", "email",
                "utility_types", "ticket", "source"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        seen = set()
        for c in contacts:
            key = (c["owner"], c["contact"], c["phone"], c["email"], c["role"])
            if key not in seen:        # the same member appears on every ticket
                seen.add(key)
                w.writerow(c)

    # --- 3. What each owner sent, and what's missing -----------------------
    by_owner = {}
    for row in rows:
        owner, _ = records.owner_of(row.get("source_file"), config.UTILITY_OWNERS)
        if owner == "811 design ticket":
            continue
        by_owner.setdefault(owner, []).append(row)
    for owner in sorted(set(ticket_members.values())):
        by_owner.setdefault(owner, [])          # on the ticket, sent nothing

    design_areas = sorted({t["design_area"].rstrip(". ") for t in tickets if t["design_area"]})
    summary = ["# Record requests: " + config.PROJECT_TITLE, "",
               "Made by step 10 on " + time.strftime("%Y-%m-%d") + ". Nothing has been sent.", ""]
    if tickets:
        summary.append("811 design tickets: " + ", ".join(t["number"] for t in tickets))
        summary += ["Design area(s):"] + ["- " + a.capitalize() for a in design_areas] + [""]
    n_drafts = 0
    for owner in sorted(by_owner):
        sheets = [r for r in by_owner[owner] if r.get("sheet_kind") != "correspondence"]
        asks = {}                                    # request key -> [sheet descriptions]
        for r in sheets:
            for need in sheet_needs(r, reviews.get(r["plan_id"])):
                asks.setdefault(need, []).append(r)
        never_datum = sheets and not any(r.get("datum_written") or r.get("coords_system") for r in sheets) \
            and not all(r.get("place_status") == "from PDF" for r in sheets)
        nothing_sent = not by_owner[owner]
        contact = owner_contact.get(owner)
        mails = [m for m in emails if records.owner_of(m["file"], config.UTILITY_OWNERS)[0] == owner]

        # Summary section
        summary.append("## " + owner)
        if nothing_sent:
            summary.append("- **Nothing received** (listed on the design ticket).")
        else:
            counts = {}
            for r in sheets:
                counts[r.get("place_status") or "not placed"] = counts.get(r.get("place_status") or "not placed", 0) + 1
            summary.append("- Sheets received: {} ({})".format(
                len(sheets), ", ".join("{} {}".format(n, s) for s, n in sorted(counts.items()))))
        if contact:
            summary.append("- Ticket contact: {} ({}), {}".format(contact["contact"] or "no name", contact["member"], contact["phone"]))
        for m in mails:
            summary.append("- Sent by email from {}{}".format(m["from"], ": automatic system, replies reach nobody" if m["automated"] else ""))
        for key in ("position", "gis_data", "date", "scale"):
            if key in asks:
                summary.append("- Ask for **{}**: {}".format(key.replace("_", " "), ", ".join(short(r["plan_id"]) for r in asks[key])))
        if never_datum:
            summary.append("- Ask for **datum**: none of their sheets state one.")
        unreviewed = [r for r in sheets if r.get("place_status") == "check" and r["plan_id"] not in reviews]
        if unreviewed:
            summary.append("- Review first (placed, not yet checked; if wrong, re-run this step to add them): "
                           + ", ".join(short(r["plan_id"]) for r in unreviewed))
        if not (asks or never_datum or nothing_sent):
            summary.append("- Nothing to ask.")
            summary.append("")
            continue
        summary.append("")

        # Draft
        n_drafts += 1
        lines = ["DRAFT - NOT SENT. Check every line before using it, and add your signature.", ""]
        if contact:
            lines.append("To:       {} ({}), phone {}".format(contact["contact"] or "design contact", contact["member"], contact["phone"]))
            lines.append("          No email address was found in the documents: call, or ask for one.")
        else:
            lines.append("To:       [no contact found in the documents: add one]")
        tix = sorted(set(contact["tickets"])) if contact else [t["number"] for t in tickets]
        lines.append("Subject:  {}: utility record information{}".format(
            config.PROJECT_TITLE, " (811 design ticket {})".format(", ".join(tix)) if tix else ""))
        lines += ["", "Hello{},".format(" " + contact["contact"].split()[0] if contact and contact["contact"]
                                        and "contact" not in contact["contact"].lower() else ""), ""]
        if nothing_sent:
            lines.append("We are mapping existing utilities for {}{}. We have not yet received any "
                         "records from you for this area. Could you send your record plans or GIS data "
                         "for the design area, or let us know if you have no facilities there?".format(
                             config.PROJECT_TITLE, " (" + "; ".join(a.lower() for a in design_areas) + ")" if design_areas else ""))
        else:
            lines.append("Thank you for the records you sent for {}. To map them accurately, could you "
                         "help with the following?".format(config.PROJECT_TITLE))
            n = 0
            for key in ("position", "gis_data", "date", "scale"):
                if key in asks:
                    n += 1
                    lines += ["", "{}. {}".format(n, REQUEST_TEXT[key])]
                    for r in asks[key]:
                        lines.append("   - {}, page {}".format(r["source_file"], r.get("page") or "?"))
            if never_datum:
                n += 1
                lines += ["", "{}. What horizontal datum and coordinate system are your records drawn in "
                          "(for example NAD 83 or NAD 27 State Plane)? If it changed over the years, "
                          "roughly when?".format(n)]
        lines += ["", "Thank you,", "[Your name]", "[Title, office]", "[Phone, email]", "",
                  "--- For you only (delete before sending): why each sheet is listed ---"]
        for r in sheets:
            review = reviews.get(r["plan_id"])
            lines.append("{}: placement {}{}; dates {}; scale {}".format(
                short(r["plan_id"]), r.get("place_status") or "none",
                ", your review: " + review[0] if review else "",
                r.get("dates_found") or "none found", r.get("scale_found") or "none found"))
        safe = re.sub(r"[^A-Za-z0-9]+", "_", owner).strip("_")
        with open(os.path.join(OUT_DIR, "draft_" + safe + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    with open(os.path.join(OUT_DIR, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary) + "\n")
    print("Wrote {} contact(s), {} draft(s) and a summary to\n   {}".format(len(seen), n_drafts, OUT_DIR))
    print("Nothing was sent. Open summary.md first.")


if __name__ == "__main__":
    main()
