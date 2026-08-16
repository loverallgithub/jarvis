# HT-004 — Create the products Google Sheet

| | |
|---|---|
| **Type** | Human task — blocking for Phase 3 (connector selection) |
| **Platform** | Google Sheets |
| **Time** | ~3 minutes |
| **Source file** | `/opt/jarvis/docs/products-inventory.csv` (59 products, parsed clean) |

---

## Why you are doing this and not me

I cannot create it. The Google Drive connector in this session is **unauthenticated** and exposes
only `authenticate` / `complete_authentication` — no sheet-creation capability. Authenticating it
needs an interactive browser consent I cannot drive.

So: I built the spreadsheet as a CSV from the verified inventory. Import is two clicks.

**If you would rather this be automated** for future regeneration, the alternative is a Google
Cloud **service account** with the Sheets API enabled, its JSON key stored as a Swarm secret, and
the sheet shared with the service account's email. Say the word and I will write that runbook
instead — it is ~20 minutes of setup and then JPD can rewrite the sheet on every inventory change.

---

## Steps

1. Get the file off the server:
   ```bash
   cat /opt/jarvis/docs/products-inventory.csv
   ```
   …or ask me to send it to you as a file and I will.

2. Go to **[sheets.new](https://sheets.new)** — creates a blank sheet instantly.

3. **File → Import → Upload** → drop the CSV.

4. In the import dialog:
   - Import location: **Replace spreadsheet**
   - Separator type: **Comma**
   - ☐ **Convert text to numbers/dates** — **leave this UNCHECKED**
     > Version strings and the `Verified` column contain dates and values Sheets will mangle
     > (`2026-07-20 200` becomes a date, `P0` stays fine, but `404` becomes a number and loses
     > context). Keep everything as text.

5. Rename the file to **`JPD Products Inventory`**.

6. Freeze the header: **View → Freeze → 1 row**.

7. Add a filter: select row 1 → **Data → Create a filter**.

8. Optional but useful — conditional formatting on the **Priority** column:
   `P0` red · `P1` orange · `P2` yellow · `P3` grey · `P4` light grey · `X` strikethrough.

---

## Columns, and what they mean

| Column | Meaning |
|---|---|
| `API?` | `Yes` / `No` / `Gated` / `Unverified` / `DEAD`. **`Gated` means an API exists but our tier cannot use it** — that is not the same as broken |
| `Verified` | The date this was last checked **from this VPS**, and what came back. Blank means never checked |
| `Live Credential?` | Whether a credential is actually present right now — **12 of 59** |
| `JPD Role` | Which pipeline step consumes it, with the step ID from `03-PIPELINE.md` |
| `Priority` | `P0` blocks the build · `P1` highest value unwired · `P2` planned · `P3` nice to have · `P4` parked · `X` dead, do not use |

---

## Verification

Paste this into a cell — it should return **59**:
```
=COUNTA(A2:A)
```

And a quick sanity check that the priorities came through:
```
=QUERY(A1:J, "select I, count(A) group by I label count(A) 'n'", 1)
```
Expected: `P0` 4 · `P1` 5 · `P2` 7 · `P3` 6 · `P4` 32 · `X` 5

---

## ⚠️ This is a reconstruction, not your list

I built this from the AppSumo triage (CHECKPOINT §4.13, 45 tools, every docs URL re-fetched on
2026-07-20) plus the live credential registry checked on 2026-08-07. **It is not the spreadsheet
you referred to** — that file does not exist anywhere on this host; I searched the whole
filesystem.

When you have your real list, replace this sheet and tell me. Connector selection sits behind an
adapter layer precisely so that swap costs almost nothing.
