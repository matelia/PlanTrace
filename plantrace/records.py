"""
Who sent each plan, and who to ask when a plan isn't enough.

  owner_of()          which utility owner a plan file came from (by its file
                      name, using config.UTILITY_OWNERS)
  read_ticket()       an 811 design ticket (UDig NY style): ticket number,
                      requester, design area, and the member list with each
                      member's phone and contact person
  read_email_page()   sender, date and subject of a printed email
  years_in()          the years in a "dates found" text

Everything comes from the documents themselves; every contact keeps the file
and page it was read from. Nothing is looked up online and nothing is sent.
"""

import re

# Words in an 811 ticket's "Utility Type(s)" column (not part of a person's name).
UTILITY_TYPE_WORDS = {
    "CATV", "FIBER", "TELEPHONE", "STREET", "LIGHTING", "WATER", "SANITARY",
    "SEWER", "STORM", "HIGHWAY", "TRAFFIC", "SIGNALS", "CULVERTS", "ELECTRIC",
    "GAS", "STEAM", "OIL", "PIPELINE", "COMMUNICATIONS", "POWER",
}
PHONE = r"\(?\d{3}\)?[\s.-]*\d{3}[\s.-]\d{4}(?:\s*x\s*\d+)?"
# Senders that are automatic systems: a reply to them reaches nobody.
AUTOMATED_SENDERS = ("irth.com", "noreply", "no-reply", "donotreply", "agt_")


def _norm(text):
    """Upper case, with underscores, dots and repeated spaces made single spaces."""
    return re.sub(r"[\s_.]+", " ", text or "").strip().upper()


def owner_of(source_file, owners):
    """(owner name, ticket member text) for a plan file, from config.UTILITY_OWNERS.
    Files that match nothing are grouped under their own file name."""
    name = _norm(source_file)
    for key, owner, ticket_text in owners:
        if _norm(key) in name:
            return owner, ticket_text
    return re.sub(r"\.[A-Za-z]+$", "", source_file or "unknown"), None


def years_in(dates_text):
    """Four-digit years (1900-2099) in a 'dates found' text, plus two-digit
    years from dates like 6-23-88 or 5/2/77. Sorted, no repeats."""
    years = set()
    # (not map/grid numbers like 1945.11)
    for y in re.findall(r"(?<![\d.])(19\d\d|20\d\d)(?![\d]|\.\d)", dates_text or ""):
        years.add(int(y))
    # month 1-12 first, so map numbers like 32-03-32 don't count
    for y in re.findall(r"(?<![\d.])(?:0?[1-9]|1[0-2])[/-]\d{1,2}[/-](\d\d)(?![\d.])", dates_text or ""):
        y = int(y)
        years.add(1900 + y if y > 30 else 2000 + y)
    return sorted(years)


def _field(text, label):
    m = re.search(r"^\s*" + label + r"\s*:\s*(.*?)(?:\s{2,}\S.*)?$", text, re.M)
    return m.group(1).strip() if m else ""


def read_ticket(page_texts):
    """Read an 811 design ticket from its PDF text layer.

    page_texts: one string per page. Returns a dict, or None if this isn't a
    ticket: number, requester {name, company, phone, email}, field_contact,
    design_area, members [{member, phone, contact, utility_types, page}]."""
    first = page_texts[0] if page_texts else ""
    m = re.search(r"Ticket\s*:\s*([\d-]{8,})", first)
    if not m:
        return None
    ticket = {
        "number": m.group(1).strip("-"),
        "requester": {
            "name": _field(first, "Contact Name"),
            "company": _field(first, "Company"),
            "phone": _field(first, "Phone"),
            "email": _field(first, "Email"),
        },
        "field_contact": _field(first, "Field Contact"),
        "design_area": "",
        "members": [],
    }
    area = re.search(r"PROPOSED DESIGN AREA:\s*(.*?)\n\s*:\s*PER CODE", first, re.S)
    if area:
        ticket["design_area"] = re.sub(r"\s*\n\s*:\s*", " ", area.group(1)).strip()

    # Member list on page 1: "NAME      555-555-1234"
    members = re.search(r"Members:\s*\n(.*)", first, re.S)
    for line in (members.group(1).splitlines() if members else []):
        mm = re.match(r"\s*(\S.*?\S)\s{2,}(" + PHONE + r")\s*$", line)
        if mm:
            ticket["members"].append({"member": mm.group(1), "phone": mm.group(2),
                                      "contact": "", "utility_types": "", "page": 1})

    # Response table (usually page 2): "<types> <CONTACT NAME>" then "(555) 555-1234"
    for page_no, text in enumerate(page_texts[1:], 2):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        for member in ticket["members"]:
            i = _member_row(lines, member)
            if i is None:
                continue
            name_line = lines[i - 1]
            if name_line.upper() == "CONTACT" and i >= 2:
                name_line = lines[i - 2] + " " + name_line
            # Utility types are the leading words; the rest is the person's name.
            words = name_line.split()
            j = 0
            while j < len(words) and words[j].strip(",").upper() in UTILITY_TYPE_WORDS:
                j += 1
            types = " ".join(words[:j])
            # Types can also wrap onto the lines just above.
            k = i - 2
            while k >= 0 and all(w.strip(",").upper() in UTILITY_TYPE_WORDS for w in lines[k].split()):
                types = lines[k] + " " + types
                k -= 1
            member["contact"] = " ".join(words[j:]).title()
            member["utility_types"] = ", ".join(t.strip() for t in types.split(",") if t.strip())
            member["page"] = page_no
    return ticket


def _member_row(lines, member):
    """Index of the line with this member's contact phone in the response
    table. Several members can share a phone (e.g. one company's gas and electric), so the
    row whose lines just above spell the member's name best wins."""
    digits = re.sub(r"\D", "", member["phone"])
    rows = [i for i, l in enumerate(lines) if i > 0 and re.sub(r"\D", "", l) == digits]
    if not rows:
        return None
    words = member["member"].upper().split()
    for k in range(len(words), 0, -1):
        tail = " ".join(words[-k:])
        for i in rows:
            if tail in " ".join(lines[max(0, i - 6):i]).upper():
                return i
    return rows[0]


def read_reviews(footprints_fc, backup_csv):
    """{plan_id: (review, review_note)}: the engineer's verdicts from the
    PlanFootprints layer, or from review_backup.csv if the layer can't be read."""
    import csv
    import os
    reviews = {}
    if os.path.exists(backup_csv):
        with open(backup_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):          # later rows are newer
                if row.get("review"):
                    reviews[row["plan_id"]] = (row["review"], row.get("review_note") or "")
    try:
        import arcpy
        if arcpy.Exists(footprints_fc):
            with arcpy.da.SearchCursor(footprints_fc, ["plan_id", "review", "review_note"]) as cur:
                for pid, review, note in cur:
                    if review:
                        reviews[pid] = (review, note or "")
    except Exception:
        pass                                      # the backup is good enough
    return reviews


def read_email_page(text):
    """Sender, date and subject of a printed email page (the header block),
    or None. 'automated' is True when the sender is a ticket system."""
    sender = re.search(r"^\s*From:\s*(\S.*)$", text, re.M)
    if not sender:
        return None
    sent = re.search(r"^\s*Sent:\s*(\S.*)$", text, re.M)
    subject = re.search(r"^\s*Subject:\s*(\S.*)$", text, re.M)
    sender = sender.group(1).strip()
    email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", sender)
    return {
        "from": sender,
        "email": email.group(0) if email else "",
        "sent": sent.group(1).strip() if sent else "",
        "subject": subject.group(1).strip() if subject else "",
        "automated": any(a in sender.lower() for a in AUTOMATED_SENDERS),
    }
