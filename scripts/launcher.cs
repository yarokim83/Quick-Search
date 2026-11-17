using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Windows.Forms;

class HpntTrayLauncher
{
    private const string AppUrl = "http://127.0.0.1:8765/";

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
        catch
        {
            return false;
        }
    }

    static void OpenBrowser()
    {
        try
        {
            Process.Start(new ProcessStartInfo(AppUrl) { UseShellExecute = true });
        }
        catch { }
    }

    static string FindAppRoot(string baseDir)
    {
        string userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string[] candidates = new[]
        {
            baseDir,
            Path.Combine(userProfile, "Desktop", "HPNT_업무검색"),
            Path.Combine(userProfile, ".gemini", "Quick Search"),
            Path.Combine(localAppData, "HPNT_Search_Standalone", "app_bundle")
        };

        foreach (string dir in candidates)
        {
            if (string.IsNullOrEmpty(dir)) continue;
            string pyw = Path.Combine(dir, "runtime", "pythonw.exe");
            string py = Path.Combine(dir, "runtime", "python.exe");
            string startPy = Path.Combine(dir, "app", "start.py");
            if ((File.Exists(pyw) || File.Exists(py)) && File.Exists(startPy))
            {
                return dir;
            }
        }
        return null;
    }

    static void TriggerRescan(NotifyIcon tray)
    {
        ThreadPool.QueueUserWorkItem(_ =>
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(AppUrl + "api/rescan");
                req.Method = "POST";
                req.ContentType = "application/json";
                req.Headers.Add("Origin", "http://127.0.0.1:8765");
                req.Timeout = 5000;
                byte[] body = Encoding.UTF8.GetBytes("{}");
                req.ContentLength = body.Length;
                using (var s = req.GetRequestStream()) s.Write(body, 0, body.Length);
                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    tray.ShowBalloonTip(2000, "HPNT 업무자료 검색", "Google Drive 목록 갱신을 시작했습니다.", ToolTipIcon.Info);
                }
            }
            catch
            {
                tray.ShowBalloonTip(2500, "HPNT 업무자료 검색", "목록 갱신 요청에 실패했습니다.", ToolTipIcon.Warning);
            }
        });
    }

    [STAThread]
    static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--check")
        {
            Console.WriteLine("HPNT_Search.exe OK");
            return 0;
        }

        if (IsPortListening("127.0.0.1", 8765))
        {
            OpenBrowser();
            return 0;
        }

        string appRoot = FindAppRoot(AppDomain.CurrentDomain.BaseDirectory);
        if (appRoot == null)
        {
            MessageBox.Show(
                "HPNT 검색 앱 파일(runtime\\pythonw.exe, app\\start.py)을 찾을 수 없습니다.",
                "HPNT 업무자료 통합 검색",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }

        string pywExe = Path.Combine(appRoot, "runtime", "pythonw.exe");
        string exePath = File.Exists(pywExe) ? pywExe : Path.Combine(appRoot, "runtime", "python.exe");
        string startPy = Path.Combine(appRoot, "app", "start.py");

        var psi = new ProcessStartInfo
        {
            FileName = exePath,
            Arguments = "-X utf8 -B \"" + startPy + "\"",
            WorkingDirectory = appRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";

        Process serverProc = null;
        try
        {
            serverProc = Process.Start(psi);
        }
        catch (Exception ex)
        {
            MessageBox.Show("검색 서버를 시작하지 못했습니다.\n" + ex.Message, "HPNT 업무자료 통합 검색", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        if (serverProc == null) return 1;

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        Icon appIcon = null;
        try { appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        if (appIcon == null) appIcon = SystemIcons.Application;

        var menu = new ContextMenu();
        var openItem = new MenuItem("검색 화면 열기 (127.0.0.1:8765)");
        var rescanItem = new MenuItem("지금 목록 갱신");
        var exitItem = new MenuItem("검색 서버 종료");

        menu.MenuItems.Add(openItem);
        menu.MenuItems.Add(rescanItem);
        menu.MenuItems.Add("-");
        menu.MenuItems.Add(exitItem);

        var tray = new NotifyIcon
        {
            Icon = appIcon,
            Text = "HPNT 업무자료 통합 검색 (백그라운드 실행 중)",
            ContextMenu = menu,
            Visible = true
        };

        Action cleanupAndExit = () =>
        {
            tray.Visible = false;
            tray.Dispose();
            try
            {
                if (serverProc != null && !serverProc.HasExited)
                {
                    serverProc.Kill();
                }
            }
            catch { }
            Application.Exit();
        };

        openItem.Click += (s, e) => OpenBrowser();
        tray.DoubleClick += (s, e) => OpenBrowser();
        rescanItem.Click += (s, e) => TriggerRescan(tray);
        exitItem.Click += (s, e) => cleanupAndExit();

        var watchTimer = new System.Windows.Forms.Timer { Interval = 1500 };
        watchTimer.Tick += (s, e) =>
        {
            if (serverProc == null || serverProc.HasExited)
            {
                watchTimer.Stop();
                tray.Visible = false;
                tray.Dispose();
                Application.Exit();
            }
        };
        watchTimer.Start();

        tray.ShowBalloonTip(2500, "HPNT 업무자료 통합 검색", "백그라운드에서 실행 중입니다. 우측 하단 트레이 아이콘으로 열거나 종료할 수 있습니다.", ToolTipIcon.Info);
        Application.Run();
        return 0;
    }
}
