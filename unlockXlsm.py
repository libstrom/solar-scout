#!/usr/bin/env python3
"""
unlockXlsm.py — Knäck VBA-lösenord + fil-kryptering på Energivision XLSM-filer

Tre klasser av filer hanteras:
  OPEN      — ingen skyddstyp, läsbar direkt (xlsm.mjs funkar redan)
  VBA-LOCK  — VBA-projektet är lösenordsskyddat, men datan är öppen
              → patchar DPB=→DPx= i vbaProject.bin (kräver ej rätt lösenord)
  ENCRYPTED — filen har fil-öppen-kryptering (AES/RC4)
              → provar lista med kandidatlösenord

Usage:
    python unlockXlsm.py scan <mapp>              # klassificera alla filer
    python unlockXlsm.py unlock <mapp> <ut-mapp>  # patcha + dekryptera
    python unlockXlsm.py unlock <fil.xlsm> <ut>   # enskild fil
"""

import sys, os, io, zipfile, shutil, struct
from pathlib import Path

try:
    import msoffcrypto
except ImportError:
    sys.exit("Kör: pip install msoffcrypto-tool")

# ── Kandidatlösenord att prova vid fil-kryptering ────────────────────────────
CANDIDATE_PASSWORDS = [
    "",
    "energivision",
    "Energivision",
    "ENERGIVISION",
    "energy",
    "1234",
    "12345",
    "password",
    "lösenord",
    "enspecta",
    "Enspecta",
    "energi",
    "deklaration",
    "energideklaration",
]


# ── Klassificering ────────────────────────────────────────────────────────────

def classify(path: Path) -> str:
    """Returnerar 'open', 'vba-lock', 'encrypted', eller 'unknown'."""
    try:
        with open(path, "rb") as f:
            office = msoffcrypto.OfficeFile(f)
            if office.is_encrypted():
                return "encrypted"
    except Exception:
        pass

    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "xl/vbaProject.bin" not in names:
                return "open"
            vba = z.read("xl/vbaProject.bin")
            if b"DPB=" in vba or b"DPx=" in vba:
                return "vba-lock"
            return "open"
    except zipfile.BadZipFile:
        return "encrypted"
    except Exception:
        return "unknown"


# ── VBA-patch: DPB=→DPx= ─────────────────────────────────────────────────────

def patch_vba_lock(src: Path, dst: Path) -> bool:
    """
    Patchar vbaProject.bin så Excel accepterar ett godtyckligt lösenord.
    Returnerar True om filen faktiskt ändrades.
    """
    try:
        with zipfile.ZipFile(src, "r") as zin:
            entries = {n: zin.read(n) for n in zin.namelist()}

        vba = entries.get("xl/vbaProject.bin", b"")
        patched = vba.replace(b"DPB=", b"DPx=")
        if patched == vba:
            # Ingen DPB-markör — kopiera ändå
            shutil.copy2(src, dst)
            return False

        entries["xl/vbaProject.bin"] = patched
        dst.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for name, data in entries.items():
                zout.writestr(name, data)
        return True
    except Exception as e:
        print(f"  PATCH FEL: {e}")
        return False


# ── Dekryptering: prova kandidatlösenord ─────────────────────────────────────

def try_decrypt(src: Path, dst: Path) -> tuple[bool, str]:
    """
    Provar CANDIDATE_PASSWORDS mot en krypterad fil.
    Returnerar (lyckades, lösenord_som_funkade).
    """
    for pwd in CANDIDATE_PASSWORDS:
        try:
            with open(src, "rb") as f:
                office = msoffcrypto.OfficeFile(f)
                office.load_key(password=pwd)
                buf = io.BytesIO()
                office.decrypt(buf)

            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(buf.getvalue())
            return True, pwd
        except Exception:
            continue
    return False, ""


# ── Scan-läge ─────────────────────────────────────────────────────────────────

def cmd_scan(folder: Path):
    files = sorted(folder.rglob("*.xlsm"))
    if not files:
        print(f"Inga .xlsm-filer hittades i {folder}")
        return

    counts = {"open": 0, "vba-lock": 0, "encrypted": 0, "unknown": 0}
    print(f"\nSkannar {len(files)} filer i {folder}\n")
    print(f"{'Typ':<12} {'Fil'}")
    print("─" * 70)
    for p in files:
        t = classify(p)
        counts[t] += 1
        icon = {"open": "✓", "vba-lock": "🔒", "encrypted": "🔐", "unknown": "?"}.get(t, "?")
        print(f"{icon} {t:<11} {p.name}")

    print("\n─" * 70)
    print(f"  ✓  Öppna (xlsm.mjs funkar direkt): {counts['open']}")
    print(f"  🔒  VBA-lås (patchas):              {counts['vba-lock']}")
    print(f"  🔐  Krypterade (provar lösenord):   {counts['encrypted']}")
    print(f"  ?   Okänd:                          {counts['unknown']}")
    print(f"\nKör 'python unlockXlsm.py unlock {folder} <ut-mapp>' för att patcha alla.")


# ── Unlock-läge ───────────────────────────────────────────────────────────────

def cmd_unlock(src: Path, out_dir: Path):
    files = [src] if src.is_file() else sorted(src.rglob("*.xlsm"))
    if not files:
        print(f"Inga filer hittades: {src}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    ok = patched = decrypted = failed = cracked_pwds = 0

    print(f"\nUnlocking {len(files)} filer → {out_dir}\n")

    for p in files:
        rel   = p.relative_to(src) if src.is_dir() else p.name
        dst   = out_dir / rel
        t     = classify(p)

        if t == "open":
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            print(f"  ✓ (öppen)    {p.name}")
            ok += 1

        elif t == "vba-lock":
            changed = patch_vba_lock(p, dst)
            status  = "patchad" if changed else "kopierad (ingen DPB)"
            print(f"  🔒 ({status}) {p.name}")
            patched += 1

        elif t == "encrypted":
            success, pwd = try_decrypt(p, dst)
            if success:
                display_pwd = f'"{pwd}"' if pwd else "(tomt)"
                print(f"  🔓 DEKRYPTERAD lösenord={display_pwd}  {p.name}")
                decrypted += 1
                cracked_pwds += 1
            else:
                print(f"  ✗ MISSLYCKADES (okänt lösenord)  {p.name}")
                failed += 1

        else:
            print(f"  ? (okänd typ)  {p.name}")
            failed += 1

    print(f"\n{'─'*60}")
    print(f"  ✓  Öppna kopierade:      {ok}")
    print(f"  🔒  VBA-lås patchade:    {patched}")
    print(f"  🔓  Krypterade knäckta:  {decrypted}")
    print(f"  ✗   Misslyckades:        {failed}")
    if cracked_pwds:
        print(f"\n  → Kör nu: node batchXlsm.mjs {out_dir} energy-data.json")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]
    if cmd == "scan":
        cmd_scan(Path(args[1]))
    elif cmd == "unlock":
        if len(args) < 3:
            sys.exit("Usage: python unlockXlsm.py unlock <källa> <ut-mapp>")
        cmd_unlock(Path(args[1]), Path(args[2]))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
