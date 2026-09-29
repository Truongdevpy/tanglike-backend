import os
import sys
import shutil
import tarfile
import urllib.request
from pathlib import Path

def ensure_node_bin() -> str:
    # 1. Existing node in PATH
    node_bin = shutil.which("node")
    if node_bin:
        return node_bin

    # 2. Local candidate binaries
    cli_dir = Path(__file__).resolve().parent
    local_candidates = [
        cli_dir / "bin" / "node",
        cli_dir / "bin" / "node.exe",
        Path.cwd() / "node",
        Path.home() / ".cache" / "node_bin" / "node",
    ]
    for cand in local_candidates:
        if cand.exists() and (os.access(str(cand), os.X_OK) or sys.platform.startswith("win")):
            return str(cand)

    # 3. Check NVM locations on Render / Linux
    for nvm_base in [
        Path.home() / ".nvm" / "versions" / "node",
        Path("/opt/render/.nvm/versions/node"),
        Path("/root/.nvm/versions/node"),
    ]:
        if nvm_base.exists():
            try:
                versions = sorted([d for d in nvm_base.iterdir() if d.is_dir()], reverse=True)
                if versions:
                    cand = versions[0] / "bin" / "node"
                    if cand.exists() and os.access(str(cand), os.X_OK):
                        return str(cand)
            except Exception:
                pass

    # 4. If on Linux (e.g. Render container), download portable official Node.js x64 binary
    if sys.platform.startswith("linux"):
        dest_dir = cli_dir / "bin"
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_node = dest_dir / "node"
        if target_node.exists() and os.access(str(target_node), os.X_OK):
            return str(target_node)

        print("[*] Downloading portable Node.js runtime for Linux x64...")
        url = "https://nodejs.org/dist/v20.18.0/node-v20.18.0-linux-x64.tar.xz"
        tmp_tar = dest_dir / "node.tar.xz"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_tar, "wb") as out_f:
                shutil.copyfileobj(resp, out_f)
            
            with tarfile.open(tmp_tar, "r:xz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith("/bin/node"):
                        extracted = tar.extractfile(member)
                        if extracted:
                            with open(target_node, "wb") as node_out:
                                shutil.copyfileobj(extracted, node_out)
                            break
            
            target_node.chmod(0o755)
            try:
                tmp_tar.unlink()
            except OSError:
                pass
                
            if target_node.exists() and os.access(str(target_node), os.X_OK):
                print("[*] Portable Node.js installed successfully at:", target_node)
                return str(target_node)
        except Exception as e:
            print(f"[-] Failed to auto-download node: {e}")

    return "node"

if __name__ == "__main__":
    print("Resolved node binary:", ensure_node_bin())
