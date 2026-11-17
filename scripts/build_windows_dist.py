"""Build native Windows EXE launcher, standalone ZIP, and single-file SFX EXE distributor."""

import ctypes
import os
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / 'dist'
PACKAGE_ROOT = 'HPNT_Search_Windows_DoubleClick'
CSC_EXE = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
ICO_SIZES = (256, 128, 64, 48, 32, 16)
APP_PNG_SIZES = (512, 192, 180)

ICON_GEN_CS = r'''
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.IO;

class IconRenderer
{
    static GraphicsPath RoundedRect(float x, float y, float w, float h, float r)
    {
        var path = new GraphicsPath();
        float d = r * 2f;
        path.AddArc(x, y, d, d, 180, 90);
        path.AddArc(x + w - d, y, d, d, 270, 90);
        path.AddArc(x + w - d, y + h - d, d, d, 0, 90);
        path.AddArc(x, y + h - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }

    static GraphicsPath AppleSquircle(float s, float pad)
    {
        // Superellipse |x|^n + |y|^n = 1 (n=4.6) matches Apple macOS/iOS continuous corner curvature.
        const int steps = 240;
        const double n = 4.6;
        double exp = 2.0 / n;
        float half = (s - pad * 2f) * 0.5f;
        float cx = s * 0.5f;
        float cy = s * 0.5f;
        var pts = new PointF[steps];
        for (int i = 0; i < steps; i++)
        {
            double t = 2.0 * Math.PI * i / steps;
            double ct = Math.Cos(t);
            double st = Math.Sin(t);
            float px = cx + (float)(Math.Sign(ct) * Math.Pow(Math.Abs(ct), exp) * half);
            float py = cy + (float)(Math.Sign(st) * Math.Pow(Math.Abs(st), exp) * half);
            pts[i] = new PointF(px, py);
        }
        var path = new GraphicsPath();
        path.AddPolygon(pts);
        return path;
    }

    static void RenderIcon(int size, string outPath)
    {
        float S = size;
        float pad = Math.Max(0.5f, S * 0.014f);
        using (var bmp = new Bitmap(size, size, PixelFormat.Format32bppArgb))
        using (var g = Graphics.FromImage(bmp))
        {
            g.Clear(Color.Transparent);
            g.SmoothingMode = SmoothingMode.HighQuality;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            g.CompositingQuality = CompositingQuality.HighQuality;
            g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;

            using (var sq = AppleSquircle(S, pad))
            {
                // Subtle Apple-style vertical teal gradient centered around HPNT #14655E
                using (var bgBrush = new LinearGradientBrush(
                    new PointF(0f, 0f), new PointF(0f, S),
                    Color.FromArgb(255, 27, 128, 119),
                    Color.FromArgb(255, 13, 77, 71)))
                {
                    g.FillPath(bgBrush, sq);
                }

                g.SetClip(sq);

                // Document card (enlarged and positioned left-balanced so "HPNT" stays unoccluded)
                float dx = 0.145f * S, dy = 0.125f * S, dw = 0.555f * S, dh = 0.695f * S, dr = 0.068f * S;
                using (var docShadow = RoundedRect(dx + 0.014f * S, dy + 0.024f * S, dw, dh, dr))
                using (var shBrush = new SolidBrush(Color.FromArgb(60, 3, 28, 25)))
                {
                    g.FillPath(shBrush, docShadow);
                }

                using (var docPath = RoundedRect(dx, dy, dw, dh, dr))
                using (var docBrush = new SolidBrush(Color.White))
                {
                    g.FillPath(docBrush, docPath);
                }

                // Document lines (#B4D8D4)
                float lineR = Math.Max(0.8f, 0.024f * S);
                float lineH = Math.Max(1.5f, 0.048f * S);
                using (var lineBrush = new SolidBrush(Color.FromArgb(255, 180, 216, 212)))
                {
                    float[] lineWidths = new[] { 0.39f * S, 0.315f * S, 0.275f * S };
                    float[] lineYs = new[] { 0.225f * S, 0.325f * S, 0.425f * S };
                    for (int i = 0; i < 3; i++)
                    {
                        using (var lp = RoundedRect(0.215f * S, lineYs[i], lineWidths[i], lineH, lineR))
                        {
                            g.FillPath(lineBrush, lp);
                        }
                    }
                }

                // HPNT wordmark inside sheet
                if (size >= 40)
                {
                    using (var font = new Font("Segoe UI", Math.Max(4f, 0.092f * S), FontStyle.Bold, GraphicsUnit.Pixel))
                    using (var txtBrush = new SolidBrush(Color.FromArgb(255, 6, 62, 58)))
                    {
                        g.DrawString("HPNT", font, txtBrush, new PointF(0.186f * S, 0.615f * S));
                    }
                }
                else
                {
                    using (var bar = RoundedRect(0.215f * S, 0.635f * S, 0.23f * S, Math.Max(1.5f, 0.06f * S), Math.Max(0.6f, 0.02f * S)))
                    using (var txtBrush = new SolidBrush(Color.FromArgb(255, 6, 62, 58)))
                    {
                        g.FillPath(txtBrush, bar);
                    }
                }

                // Magnifying glass bottom-right
                float mx = 0.680f * S;
                float my = 0.640f * S;
                float mr = 0.168f * S;
                float hx1 = mx + mr * 0.72f;
                float hy1 = my + mr * 0.72f;
                float hx2 = 0.870f * S;
                float hy2 = 0.845f * S;
                float handleW = Math.Max(2f, 0.078f * S);
                float ringW = Math.Max(1.5f, 0.056f * S);

                // Soft shadow under lens & handle
                using (var shHandle = new Pen(Color.FromArgb(56, 3, 28, 25), handleW))
                {
                    shHandle.StartCap = LineCap.Round;
                    shHandle.EndCap = LineCap.Round;
                    g.DrawLine(shHandle, hx1 + 0.01f * S, hy1 + 0.018f * S, hx2 + 0.01f * S, hy2 + 0.018f * S);
                }
                using (var shBrush = new SolidBrush(Color.FromArgb(56, 3, 28, 25)))
                {
                    float sr = mr + ringW * 0.45f;
                    g.FillEllipse(shBrush, mx - sr + 0.012f * S, my - sr + 0.020f * S, sr * 2f, sr * 2f);
                }

                // Handle
                using (var hPen = new Pen(Color.FromArgb(255, 6, 62, 58), handleW))
                {
                    hPen.StartCap = LineCap.Round;
                    hPen.EndCap = LineCap.Round;
                    g.DrawLine(hPen, hx1, hy1, hx2, hy2);
                }

                // Lens fill & highlight
                using (var lensBrush = new LinearGradientBrush(
                    new PointF(mx - mr, my - mr), new PointF(mx + mr, my + mr),
                    Color.FromArgb(255, 232, 246, 243),
                    Color.FromArgb(255, 204, 232, 227)))
                {
                    g.FillEllipse(lensBrush, mx - mr, my - mr, mr * 2f, mr * 2f);
                }
                if (size >= 32)
                {
                    using (var glossPen = new Pen(Color.FromArgb(190, 255, 255, 255), Math.Max(1f, 0.022f * S)))
                    {
                        glossPen.StartCap = LineCap.Round;
                        glossPen.EndCap = LineCap.Round;
                        float gr = mr * 0.66f;
                        g.DrawArc(glossPen, mx - gr, my - gr, gr * 2f, gr * 2f, 200f, 65f);
                    }
                }

                // Outer bezel ring
                using (var ringPen = new Pen(Color.FromArgb(255, 6, 62, 58), ringW))
                {
                    g.DrawEllipse(ringPen, mx - mr, my - mr, mr * 2f, mr * 2f);
                }

                // Crisp Apple-style specular rim clipped strictly inside the squircle edge
                using (var rimPen = new Pen(Color.FromArgb(46, 255, 255, 255), Math.Max(1f, S * 0.012f)))
                {
                    g.DrawPath(rimPen, sq);
                }

                g.ResetClip();
            }

            bmp.Save(outPath, ImageFormat.Png);
        }
    }

    static int Main(string[] args)
    {
        for (int i = 0; i + 1 < args.Length; i += 2)
        {
            int size = int.Parse(args[i]);
            string path = args[i + 1];
            string dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            RenderIcon(size, path);
        }
        return 0;
    }
}
'''

ROOT_FILES = (
    'HPNT_Start.cmd',
    'HPNT_Import_Notes.cmd',
    'HPNT_Search.exe',
    '사용안내.txt',
    '구성요소_출처.txt',
    'VERSION',
    'CHANGELOG.md',
)
SKIP_DIRS = frozenset(('__pycache__', '.git', '.agents', 'backups', 'dist'))
SKIP_FILES = frozenset(('desktop.ini', 'thumbs.db'))


def read_version():
    ver = ROOT_DIR / 'VERSION'
    return ver.read_text(encoding='utf-8').strip() if ver.is_file() else '0.0.0'


def should_skip_file(name):
    low = name.lower()
    return low in SKIP_FILES or low.endswith(('.pyc', '.pyo')) or name.startswith('~$')


def refresh_shell_icons(target_path=None):
    """Ask Windows Explorer shell to invalidate cached icons immediately."""
    try:
        shell32 = ctypes.windll.shell32
        if target_path is not None:
            shell32.SHChangeNotify(0x00002000, 0x0005, ctypes.c_wchar_p(str(target_path)), None)
        shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass


def ensure_ico():
    """Render Apple-style full-bleed squircle PNGs and pack multi-resolution app/hpnt_icon.ico."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    app_dir = ROOT_DIR / 'app'
    ico_path = app_dir / 'hpnt_icon.ico'

    cs_path = DIST_DIR / '_icon_gen.cs'
    gen_exe = DIST_DIR / '_icon_gen.exe'
    temp_pngs = []
    try:
        cs_path.write_text(ICON_GEN_CS, encoding='utf-8')
        compile_cs(cs_path, gen_exe, extra_refs=('System.Drawing.dll',), target='exe')

        cmd = [str(gen_exe)]
        for s in APP_PNG_SIZES:
            cmd.extend([str(s), str(app_dir / f'icon-{s}.png')])
        for s in ICO_SIZES:
            tmp_png = DIST_DIR / f'_ico_{s}.png'
            temp_pngs.append((s, tmp_png))
            cmd.extend([str(s), str(tmp_png)])

        subprocess.run(cmd, check=True)

        frames = [(s, p.read_bytes()) for s, p in temp_pngs]
        header = struct.pack('<HHH', 0, 1, len(frames))
        offset = 6 + 16 * len(frames)
        entries = bytearray()
        for size, png_data in frames:
            dim = 0 if size >= 256 else size
            entries.extend(struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(png_data), offset))
            offset += len(png_data)
        ico_path.write_bytes(header + bytes(entries) + b''.join(data for _, data in frames))
        print(f'[Icon] Generated Apple squircle PNGs ({APP_PNG_SIZES}) and multi-res ICO ({ICO_SIZES})')
    finally:
        cs_path.unlink(missing_ok=True)
        gen_exe.unlink(missing_ok=True)
        for _, p in temp_pngs:
            p.unlink(missing_ok=True)

    return ico_path


def compile_cs(cs_file, out_exe, ico_path=None, extra_refs=(), target='winexe'):
    if not CSC_EXE.is_file():
        raise FileNotFoundError(f'C# compiler not found: {CSC_EXE}')
    cmd = [
        str(CSC_EXE),
        '/nologo',
        '/optimize+',
        f'/target:{target}',
        '/platform:x64',
        f'/out:{out_exe}',
    ]
    if ico_path and ico_path.is_file():
        cmd.append(f'/win32icon:{ico_path}')
    for ref in extra_refs:
        cmd.append(f'/reference:{ref}')
    cmd.append(str(cs_file))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f'csc failed for {cs_file.name}:\n{res.stdout}\n{res.stderr}')
    print(f'[Compile] Built {out_exe.name} ({target})')


def add_dir_to_zip(zf, src_dir, zip_prefix):
    count = 0
    for dp, ds, fs in os.walk(src_dir):
        ds[:] = sorted(d for d in ds if d not in SKIP_DIRS and not d.startswith('.'))
        dp_path = Path(dp)
        for fn in sorted(fs):
            if should_skip_file(fn):
                continue
            full = dp_path / fn
            rel = full.relative_to(src_dir).as_posix()
            zf.write(full, f'{zip_prefix}/{rel}')
            count += 1
    return count


def build_all():
    version = read_version()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if not (ROOT_DIR / 'runtime' / 'python.exe').is_file():
        raise FileNotFoundError('runtime/python.exe not found; cannot build standalone package.')

    ico_path = ensure_ico()

    # 1. Build folder tray launcher HPNT_Search.exe (winexe, no terminal window)
    launcher_exe = ROOT_DIR / 'HPNT_Search.exe'
    compile_cs(
        ROOT_DIR / 'scripts' / 'launcher.cs',
        launcher_exe,
        ico_path=ico_path,
        extra_refs=('System.Windows.Forms.dll', 'System.Drawing.dll'),
        target='winexe',
    )
    chk1 = subprocess.run([str(launcher_exe), '--check'], capture_output=True, text=True, check=True)
    print(f'[Verify] HPNT_Search.exe --check rc={chk1.returncode} {chk1.stdout.strip()}')

    temp_dir = Path(tempfile.gettempdir())
    for stale in temp_dir.glob('hpnt_running_*.exe'):
        try:
            stale.unlink()
        except OSError:
            pass

    legacy_desktop_launcher = Path.home() / 'Desktop' / 'HPNT 업무검색.exe'
    if legacy_desktop_launcher.is_file():
        try:
            legacy_desktop_launcher.unlink()
            print(f'[Cleanup] Removed legacy Desktop launcher: {legacy_desktop_launcher}')
        except PermissionError:
            staged = temp_dir / f'hpnt_running_{int(time.time())}.exe'
            os.replace(legacy_desktop_launcher, staged)
            print(f'[Cleanup] Moved running legacy Desktop launcher to {staged.name}')
        refresh_shell_icons(legacy_desktop_launcher)

    # 2. Build standalone ZIP package
    zip_name = f'HPNT_Search_Windows_v{version}.zip'
    dist_zip = DIST_DIR / zip_name
    added = 0
    with zipfile.ZipFile(dist_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fname in ROOT_FILES:
            fpath = ROOT_DIR / fname
            if fpath.is_file():
                zf.write(fpath, f'{PACKAGE_ROOT}/{fname}')
                added += 1
        added += add_dir_to_zip(zf, ROOT_DIR / 'app', f'{PACKAGE_ROOT}/app')
        added += add_dir_to_zip(zf, ROOT_DIR / 'runtime', f'{PACKAGE_ROOT}/runtime')

    with zipfile.ZipFile(dist_zip, 'r') as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f'ZIP integrity check failed at: {bad}')
        names = set(zf.namelist())
        required = (
            f'{PACKAGE_ROOT}/HPNT_Start.cmd',
            f'{PACKAGE_ROOT}/HPNT_Search.exe',
            f'{PACKAGE_ROOT}/runtime/python.exe',
            f'{PACKAGE_ROOT}/app/start.py',
            f'{PACKAGE_ROOT}/app/server.py',
            f'{PACKAGE_ROOT}/app/catalog.py',
            f'{PACKAGE_ROOT}/app/mail_archive.py',
            f'{PACKAGE_ROOT}/app/index.html',
        )
        missing = [r for r in required if r not in names]
        if missing:
            raise RuntimeError(f'Missing required files in ZIP: {missing}')

    zip_mb = dist_zip.stat().st_size / (1024 * 1024)
    print(f'[Build] Created & verified ZIP: {dist_zip} ({added} files, {zip_mb:.2f} MB)')

    # 3. Build single-file SFX standalone EXE (winexe, no terminal window)
    sfx_stub = DIST_DIR / 'sfx_stub.exe'
    compile_cs(
        ROOT_DIR / 'scripts' / 'standalone_sfx.cs',
        sfx_stub,
        ico_path=ico_path,
        extra_refs=(
            'System.IO.Compression.dll',
            'System.IO.Compression.FileSystem.dll',
            'System.Windows.Forms.dll',
        ),
        target='winexe',
    )
    exe_name = f'HPNT_Search_Windows_v{version}.exe'
    dist_exe = DIST_DIR / exe_name

    stub_bytes = sfx_stub.read_bytes()
    zip_bytes = dist_zip.read_bytes()
    payload_offset = len(stub_bytes)
    payload_length = len(zip_bytes)
    footer = struct.pack('<qq8s', payload_offset, payload_length, b'HPNTSFX1')
    dist_exe.write_bytes(stub_bytes + zip_bytes + footer)
    sfx_stub.unlink(missing_ok=True)

    chk2 = subprocess.run([str(dist_exe), '--check'], capture_output=True, text=True, check=True)
    exe_mb = dist_exe.stat().st_size / (1024 * 1024)
    print(f'[Build] Created & verified SFX EXE: {dist_exe} ({exe_mb:.2f} MB) -> {chk2.stdout.strip()}')

    # 4. Copy ZIP and SFX EXE to Desktop and Google Drive, cleaning old generated bundles
    for artifact in (dist_zip, dist_exe):
        for dest_dir in (Path.home() / 'Desktop', Path(r'G:\내 드라이브\HPNT_업무')):
            if dest_dir.is_dir():
                target = dest_dir / artifact.name
                shutil.copy2(artifact, target)
                print(f'[Copy] Saved to: {target}')
                for old in dest_dir.glob(f'HPNT_Search_Windows_v*{artifact.suffix}'):
                    if old.name != artifact.name:
                        try:
                            old.unlink()
                            print(f'[Cleanup] Removed old bundle: {old}')
                        except OSError:
                            pass
                if artifact.suffix == '.exe' and dest_dir == Path.home() / 'Desktop':
                    refresh_shell_icons(target)

    return dist_zip, dist_exe


if __name__ == '__main__':
    build_all()
