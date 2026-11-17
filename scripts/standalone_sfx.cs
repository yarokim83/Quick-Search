using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

class SliceStream : Stream
{
    private readonly Stream _inner;
    private readonly long _offset;
    private readonly long _length;
    private long _pos;

    public SliceStream(Stream inner, long offset, long length)
    {
        _inner = inner;
        _offset = offset;
        _length = length;
        _inner.Seek(offset, SeekOrigin.Begin);
    }

    public override bool CanRead { get { return true; } }
    public override bool CanSeek { get { return true; } }
    public override bool CanWrite { get { return false; } }
    public override long Length { get { return _length; } }
    public override long Position
    {
        get { return _pos; }
        set { Seek(value, SeekOrigin.Begin); }
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        long remain = _length - _pos;
        if (remain <= 0) return 0;
        if (count > remain) count = (int)remain;
        _inner.Seek(_offset + _pos, SeekOrigin.Begin);
        int read = _inner.Read(buffer, offset, count);
        _pos += read;
        return read;
    }

    public override long Seek(long offset, SeekOrigin origin)
    {
        long next = origin == SeekOrigin.Begin ? offset
                  : origin == SeekOrigin.Current ? _pos + offset
                  : _length + offset;
        if (next < 0) next = 0;
        if (next > _length) next = _length;
        _pos = next;
        return _pos;
    }

    public override void Flush() { }
    public override void SetLength(long value) { throw new NotSupportedException(); }
    public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
}

class HpntStandaloneSfx
{
    private const string Magic = "HPNTSFX1";
    private const string Prefix = "HPNT_Search_Windows_DoubleClick/";

    static bool IsPortListening(string host, int port)
    {
        try
        {
            using (var client = new TcpClient())
            {
                var ar = client.BeginConnect(host, port, null, null);
                bool ok = ar.AsyncWaitHandle.WaitOne(TimeSpan.FromMilliseconds(250));
                if (!ok) return false;
                client.EndConnect(ar);
                return true;
            }
        }
        catch { return false; }
    }

    static bool TryReadFooter(FileStream fs, out long payloadOffset, out long payloadLength)
    {
        payloadOffset = 0;
        payloadLength = 0;
        if (fs.Length < 24) return false;
        fs.Seek(-24, SeekOrigin.End);
        using (var br = new BinaryReader(fs, Encoding.ASCII, true))
        {
            payloadOffset = br.ReadInt64();
            payloadLength = br.ReadInt64();
            string tag = Encoding.ASCII.GetString(br.ReadBytes(8));
            if (tag != Magic) return false;
            if (payloadOffset <= 0 || payloadLength <= 0 || payloadOffset + payloadLength > fs.Length - 24) return false;
            return true;
        }
    }

    static string ReadZipVersion(ZipArchive archive)
    {
        var verEntry = archive.GetEntry(Prefix + "VERSION");
        if (verEntry == null) return "";
        using (var sr = new StreamReader(verEntry.Open(), Encoding.UTF8))
        {
            return sr.ReadToEnd().Trim();
        }
    }

    [STAThread]
    static int Main(string[] args)
    {
        string selfExe = Assembly.GetExecutingAssembly().Location;

        using (var fs = new FileStream(selfExe, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            long offset, length;
            if (!TryReadFooter(fs, out offset, out length))
            {
                MessageBox.Show("배포 패키지가 손상되었거나 페이로드가 없습니다.", "HPNT 업무자료 통합 검색", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }

            if (args.Length > 0 && args[0] == "--check")
            {
                using (var slice = new SliceStream(fs, offset, length))
                using (var archive = new ZipArchive(slice, ZipArchiveMode.Read))
                {
                    Console.WriteLine("SFX_OK version=" + ReadZipVersion(archive) + " entries=" + archive.Entries.Count);
                }
                return 0;
            }

            string localData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string bundleDir = Path.Combine(localData, "HPNT_Search_Standalone", "app_bundle");
            string bundleExe = Path.Combine(bundleDir, "HPNT_Search.exe");
            string bundleVerFile = Path.Combine(bundleDir, "VERSION");
            string currentVer = File.Exists(bundleVerFile) ? File.ReadAllText(bundleVerFile, Encoding.UTF8).Trim() : "";

            using (var slice = new SliceStream(fs, offset, length))
            using (var archive = new ZipArchive(slice, ZipArchiveMode.Read))
            {
                string pkgVer = ReadZipVersion(archive);
                bool alreadyRunning = IsPortListening("127.0.0.1", 8765);
                if (!File.Exists(bundleExe) || currentVer != pkgVer)
                {
                    Directory.CreateDirectory(bundleDir);
                    string fullBundleRoot = Path.GetFullPath(bundleDir) + Path.DirectorySeparatorChar;
                    ZipArchiveEntry versionEntry = null;
                    bool allExtracted = true;

                    foreach (var entry in archive.Entries)
                    {
                        if (string.IsNullOrEmpty(entry.Name)) continue;
                        string rel = entry.FullName.StartsWith(Prefix, StringComparison.Ordinal)
                            ? entry.FullName.Substring(Prefix.Length)
                            : entry.FullName;
                        if (rel == "VERSION")
                        {
                            versionEntry = entry;
                            continue;
                        }
                        string targetPath = Path.GetFullPath(Path.Combine(bundleDir, rel.Replace('/', Path.DirectorySeparatorChar)));
                        if (!targetPath.StartsWith(fullBundleRoot, StringComparison.OrdinalIgnoreCase)) continue;
                        Directory.CreateDirectory(Path.GetDirectoryName(targetPath));
                        try
                        {
                            entry.ExtractToFile(targetPath, true);
                        }
                        catch
                        {
                            if (!alreadyRunning) throw;
                            allExtracted = false;
                        }
                    }

                    if (versionEntry != null && (!alreadyRunning || allExtracted))
                    {
                        string verTarget = Path.GetFullPath(Path.Combine(bundleDir, "VERSION"));
                        try { versionEntry.ExtractToFile(verTarget, true); }
                        catch { if (!alreadyRunning) throw; }
                    }
                }

                if (alreadyRunning)
                {
                    Process.Start(new ProcessStartInfo("http://127.0.0.1:8765/") { UseShellExecute = true });
                    return 0;
                }
            }

            var psi = new ProcessStartInfo
            {
                FileName = bundleExe,
                WorkingDirectory = bundleDir,
                UseShellExecute = false
            };
            Process.Start(psi);
            return 0;
        }
    }
}
