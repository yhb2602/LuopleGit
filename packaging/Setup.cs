using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

class Setup {
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr SendMessageTimeout(IntPtr h, uint msg, IntPtr wp, string lp, uint flags, uint timeout, out IntPtr result);
    static int Main(string[] args) {
        Console.OutputEncoding = Encoding.UTF8;
        // Extraction mode is used for repeatable packaging smoke tests. It never changes PATH.
        bool extract = args.Length == 2 && args[0] == "--extract-to";
        string parent = extract ? Path.GetFullPath(args[1]) : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs");
        // Upgrade in the legacy location so existing terminals and PATH keep working.
        string legacy = Path.Combine(parent, "LupleGit");
        string destination = extract ? parent : Path.Combine(parent, "LuopleGit");
        if (!extract && !Directory.Exists(destination) && Directory.Exists(legacy)) destination = legacy;
        string stage = destination + ".new-" + Guid.NewGuid().ToString("N");
        string backup = destination + ".previous-" + DateTime.Now.ToString("yyyyMMddHHmmss");
        bool moved = false;
        try {
            Console.WriteLine("Luople Git 설치 중... Python과 Git이 포함되어 있습니다.");
            Directory.CreateDirectory(stage);
            using (Stream input = Assembly.GetExecutingAssembly().GetManifestResourceStream("luople.zip"))
            using (var zip = new ZipArchive(input, ZipArchiveMode.Read)) {
                foreach (var entry in zip.Entries) {
                    string target = Path.GetFullPath(Path.Combine(stage, entry.FullName));
                    if (!target.StartsWith(Path.GetFullPath(stage) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new IOException("Unsafe archive path");
                    if (entry.FullName.EndsWith("/")) { Directory.CreateDirectory(target); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    entry.ExtractToFile(target);
                }
            }
            if (Directory.Exists(destination)) { Directory.Move(destination, backup); moved = true; }
            Directory.Move(stage, destination);
            if (!extract) {
                string path = Environment.GetEnvironmentVariable("Path", EnvironmentVariableTarget.User) ?? "";
                if (!path.Split(';').Any(p => p.TrimEnd('\\').Equals(destination, StringComparison.OrdinalIgnoreCase))) {
                    Environment.SetEnvironmentVariable("Path", path.TrimEnd(';') + ";" + destination, EnvironmentVariableTarget.User);
                    IntPtr result; SendMessageTimeout(new IntPtr(0xffff), 0x1a, IntPtr.Zero, "Environment", 2, 3000, out result);
                }
                Console.WriteLine("설치 완료. 새 PowerShell 창을 열고 프로젝트 폴더에서 lu를 실행하세요.");
                Console.WriteLine("기존 터미널의 PATH는 갱신되지 않습니다. 새 창에서도 안 되면 터미널 앱을 완전히 종료한 뒤 다시 여세요.");
            }
            Console.WriteLine(destination);
            if (moved) Console.WriteLine("이전 설치 보존: " + backup);
            if (!extract) { Console.WriteLine("Enter를 누르면 닫습니다."); Console.ReadLine(); }
            return 0;
        } catch (Exception ex) {
            if (moved && !Directory.Exists(destination) && Directory.Exists(backup)) Directory.Move(backup, destination);
            Console.Error.WriteLine("설치 실패: " + ex.Message);
            Console.Error.WriteLine("진행 중인 루플과 자동 저장을 종료한 뒤 다시 시도하세요. 프로젝트 파일은 변경하지 않았습니다.");
            if (!extract) Console.ReadLine();
            return 1;
        }
    }
}
