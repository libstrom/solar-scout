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
    python unlockXlsm.py scan <mapp>                       # klassificera alla filer
    python unlockXlsm.py unlock <mapp> <ut-mapp>           # patcha + dekryptera
    python unlockXlsm.py unlock <fil.xlsm> <ut>            # enskild fil
    python unlockXlsm.py crack <mapp> <wordlist.txt>        # ordlisteattack på krypterade
    python unlockXlsm.py crack <fil.xlsm> <wordlist.txt>   # enskild fil
    python unlockXlsm.py john <mapp>                       # extrahera hashar → john/hashcat
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
    "Energy",
    "1234",
    "12345",
    "123456",
    "password",
    "Password",
    "lösenord",
    "Lösenord",
    "enspecta",
    "Enspecta",
    "ENSPECTA",
    "energi",
    "Energi",
    "deklaration",
    "energideklaration",
    "Energideklaration",
    "ev",
    "EV",
    "ev2023",
    "ev2024",
    "ev2025",
    "energivision2023",
    "energivision2024",
    "Energivision2023",
    "Energivision2024",
    "villa",
    "Villa",
    "fastighet",
    "Fastighet",
    "brf",
    "BRF",
    "bygg",
    "Bygg",
    "sverige",
    "Sverige",
    "admin",
    "Admin",
    "test",
    "Test",
    "qwerty",
    "abc123",
    "secret",
    "Secret",
    "default",
    "Default",
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


# ── Ordlisteattack (crack-läge) ───────────────────────────────────────────────

def try_password(src: Path, pwd: str) -> bool:
    try:
        with open(src, "rb") as f:
            office = msoffcrypto.OfficeFile(f)
            office.load_key(password=pwd)
            buf = io.BytesIO()
            office.decrypt(buf)
        return True
    except Exception:
        return False


def cmd_crack(src: Path, wordlist_path: Path):
    """Ordlisteattack mot krypterade filer. Hittar lösenordet — dekrypterar ej."""
    files = [src] if src.is_file() else [
        p for p in sorted(src.rglob("*.xlsm")) if classify(p) == "encrypted"
    ]
    if not files:
        print("Inga krypterade filer hittades.")
        return

    try:
        passwords = wordlist_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except FileNotFoundError:
        sys.exit(f"Ordlista saknas: {wordlist_path}")

    passwords = [p.strip() for p in passwords if p.strip()]
    print(f"\nKnäcker {len(files)} krypterade filer med {len(passwords)} lösenord...\n")

    sample = files[0]
    print(f"Provar mot: {sample.name}")
    for i, pwd in enumerate(passwords, 1):
        if i % 100 == 0:
            print(f"  {i}/{len(passwords)}: {pwd!r}", end="\r")
        if try_password(sample, pwd):
            print(f"\n\n  ✅ LÖSENORD HITTAT: {pwd!r}")
            print(f"\n  Verifiera mot alla {len(files)} filer:")
            found_all = sum(1 for f in files if try_password(f, pwd))
            print(f"  Funkar på {found_all}/{len(files)} filer")
            print(f"\n  Kör nu:")
            print(f'  python unlockXlsm.py unlock <källa> unlocked_xlsm')
            print(f'  (lägg till "{pwd}" i CANDIDATE_PASSWORDS i unlockXlsm.py)')
            return

    print(f"\n  ✗ Inget lösenord i ordlistan matchade. Prova hashcat (se nedan).")
    print(f"\n  Extrahera hash: python unlockXlsm.py john {src}")


# ── John/Hashcat hash-extraktion ──────────────────────────────────────────────

def cmd_john(src: Path):
    """Extraherar office2john-kompatibla hashar för hashcat/john."""
    try:
        import msoffcrypto.method.ecma376_agile as _  # noqa: check exists
    except ImportError:
        pass

    files = [src] if src.is_file() else [
        p for p in sorted(src.rglob("*.xlsm")) if classify(p) == "encrypted"
    ]
    if not files:
        print("Inga krypterade filer hittades.")
        return

    print(f"\n  {len(files)} krypterade filer hittade.")
    print(f"\n  Steg 1 — Extrahera hash (kräver office2john från John the Ripper):")
    print(f"  Ladda ner: https://github.com/openwall/john/blob/bleeding-jumbo/run/office2john.py")
    print()
    sample = files[0]
    print(f'  python office2john.py "{sample}" > hash.txt')
    print(f'  # eller alla på en gång (PowerShell):')
    print(f'  Get-ChildItem -Recurse -Filter "*.xlsm" | ForEach-Object {{')
    print(f'    python office2john.py $_.FullName >> all_hashes.txt')
    print(f'  }}')
    print()
    print(f"  Steg 2 — Knäck med hashcat (GPU, snabbast):")
    print(f"  hashcat -m 9600 hash.txt wordlist.txt   # Office 2013+ AES-128")
    print(f"  hashcat -m 9500 hash.txt wordlist.txt   # Office 2010")
    print()
    print(f"  Steg 3 — Eller John the Ripper (CPU):")
    print(f"  john --wordlist=wordlist.txt hash.txt")
    print()
    print(f"  Ordlista tips:")
    print(f"  - rockyou.txt (standard, finns i Kali/hashcat downloads)")
    print(f"  - Sätt ihop egen: echo -e 'energivision\\nEnspecta\\nev2023' > ev_wordlist.txt")
    print()
    print(f"  Tips: Kolla om lösenordet finns i Energivision-installationen:")
    print(f'  reg query HKLM\\SOFTWARE\\Energivision /s')
    print(f'  dir "C:\\Program Files\\Energivision\\" /s /b | findstr /i "*.cfg *.ini *.xml"')


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
    elif cmd == "crack":
        if len(args) < 3:
            sys.exit("Usage: python unlockXlsm.py crack <källa> <wordlist.txt>")
        cmd_crack(Path(args[1]), Path(args[2]))
    elif cmd == "john":
        cmd_john(Path(args[1]))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
