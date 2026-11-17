using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Text;

class HpntLauncher
{
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
            string py = Path.Combine(dir, "runtime", "python.exe");
            string startPy = Path.Combine(dir, "app", "start.py");
            if (File.Exists(py) && File.Exists(startPy))
            {
                return dir;
            }
        }
        return null;
    }

    static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;
        if (args.Length > 0 && args[0] == "--check")
        {
            Console.WriteLine("HPNT_Search.exe OK");
            return 0;
        }

        if (IsPortListening("127.0.0.1", 8765))
        {
            Console.WriteLine("HPNT 검색 서버가 이미 실행 중입니다. 브라우저를 엽니다...");
            Process.Start(new ProcessStartInfo("http://127.0.0.1:8765/") { UseShellExecute = true });
            return 0;
        }

        string appRoot = FindAppRoot(AppDomain.CurrentDomain.BaseDirectory);
        if (appRoot == null)
        {
            Console.Error.WriteLine("HPNT 검색 앱 파일(runtime\\python.exe, app\\start.py)을 찾을 수 없습니다.");
            Console.WriteLine("계속하려면 아무 키나 누르세요...");
            try { Console.ReadKey(true); } catch { }
            return 1;
        }

        string pyExe = Path.Combine(appRoot, "runtime", "python.exe");
        string startPy = Path.Combine(appRoot, "app", "start.py");

        var psi = new ProcessStartInfo
        {
            FileName = pyExe,
            Arguments = "-X utf8 -B \"" + startPy + "\"",
            WorkingDirectory = appRoot,
            UseShellExecute = false
        };
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";

        using (var proc = Process.Start(psi))
        {
            if (proc == null)
            {
                Console.Error.WriteLine("검색 서버를 시작하지 못했습니다.");
                return 1;
            }
            proc.WaitForExit();
            if (proc.ExitCode != 0)
            {
                Console.Error.WriteLine("검색 앱이 종료되었습니다 (종료 코드: " + proc.ExitCode + ").");
                Console.WriteLine("계속하려면 아무 키나 누르세요...");
                try { Console.ReadKey(true); } catch { }
            }
            return proc.ExitCode;
        }
    }
}
