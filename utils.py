import re

def short_addr(addr: str | None, keep: int = 3) -> str:
    if not addr:
        return "Unknown"
    addr = addr.strip()
    if len(addr) <= keep*2 + 3:
        return addr
    return f"{addr[:keep]}...{addr[-keep:]}"

def tonviewer_tx_link(tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    return f"https://tonviewer.com/transaction/{tx_hash.strip()}"

def safe_symbol(sym: str) -> str:
    sym = sym.strip()
    sym = re.sub(r"[^A-Za-z0-9_$]", "", sym)
    return sym[:16] if sym else "TOKEN"

def nano_to_ton(n: int | str | None) -> float | None:
    try:
        if n is None:
            return None
        return int(n) / 1e9
    except Exception:
        return None

def nano_to_units(n: int | str | None, decimals: int) -> float | None:
    try:
        if n is None:
            return None
        return int(n) / (10 ** decimals)
    except Exception:
        return None
