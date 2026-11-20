"""Optional, local read-only MCP service for authorized reverse engineering.

Run from a local checkout: WORKSPACE=/path/to/owned-inputs python mcp/server.py
This is an MCP server for compatible clients, NOT a public HTTP endpoint.
"""
import os
from pathlib import Path
import subprocess
import zipfile
from mcp.server.fastmcp import FastMCP

mcp=FastMCP('Authorized Android RE Tools')
ROOT=Path(os.environ.get('WORKSPACE',str(Path.cwd()))).resolve()
GHIDRA_HOME=os.environ.get('GHIDRA_HOME','')


def inside(relative: str) -> Path:
    if not relative or Path(relative).is_absolute():raise ValueError('Relative path inside WORKSPACE required')
    p=(ROOT/relative).resolve(strict=True)
    p.relative_to(ROOT)
    if not p.is_file() or p.is_symlink():raise ValueError('Expected a regular, non-symlink file')
    return p


def call(args,timeout=120):
    p=subprocess.run(args,capture_output=True,text=True,errors='replace',timeout=timeout,check=False)
    return {'code':p.returncode,'stdout':p.stdout[:12000],'stderr':p.stderr[:5000]}


@mcp.tool()
def apk_inventory(file: str) -> dict:
    """List an owned APK's DEX files and native ELF libraries without executing it."""
    p=inside(file)
    if p.suffix.lower()!='.apk':raise ValueError('APK required')
    with zipfile.ZipFile(p) as z:
        info=z.infolist()
        if len(info)>20000 or sum(x.file_size for x in info)>750_000_000:raise ValueError('Archive exceeds limits')
        names=z.namelist()
        return {'classes':[x for x in names if x.startswith('classes') and x.endswith('.dex')][:80],
                'native_libraries':[x for x in names if x.startswith('lib/') and x.endswith('.so')][:150],
                'total_entries':len(info)}


@mcp.tool()
def elf_symbols(file: str) -> dict:
    """Return readelf header and dynamic symbol table of an authorized ARM64 .so."""
    p=inside(file)
    if p.suffix.lower()!='.so':raise ValueError('Native .so required')
    if p.open('rb').read(4)!=b'\x7fELF':raise ValueError('Invalid ELF magic')
    return {'header':call(['readelf','-h',str(p)]),'symbols':call(['readelf','-Ws',str(p)])}


@mcp.tool()
def ghidra_headless_import(file: str) -> dict:
    """Analyze a local .so in Ghidra headless when GHIDRA_HOME has been configured."""
    p=inside(file)
    if p.suffix.lower()!='.so':raise ValueError('Native .so required')
    binary=Path(GHIDRA_HOME)/'support'/'analyzeHeadless'
    if not binary.is_file():raise RuntimeError('Install Ghidra and set GHIDRA_HOME first')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='re-ghidra-') as tmp:
        return call([str(binary),tmp,'AuthorizedRE','-import',str(p),'-analysisTimeoutPerFile','180','-deleteProject'],timeout=240)


if __name__=='__main__':mcp.run(transport='stdio')
