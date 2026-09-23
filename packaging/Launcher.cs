using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

class Launcher {
    static string Quote(string value) {
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append('"'); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"'); return result.ToString();
    }
    [STAThread]
    static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        try {
            string python = Path.Combine(root, "runtime", "python", "python.exe");
            if (!File.Exists(python)) { Console.Error.WriteLine("Runtime missing. Extract the complete Luople package."); return 1; }
            string cwd = Environment.CurrentDirectory;
            if (args.Length == 0 && Path.GetFullPath(cwd).TrimEnd('\\') == root.TrimEnd('\\')) {
                using (var dialog = new FolderBrowserDialog()) {
                    dialog.Description = "루플로 관리할 프로젝트 폴더를 선택하세요";
                    if (dialog.ShowDialog() != DialogResult.OK) return 0;
                    cwd = dialog.SelectedPath;
                }
            }
            var command = new StringBuilder("-X utf8 " + Quote(Path.Combine(root, "lu.py")));
            foreach (string arg in args) { command.Append(" "); command.Append(Quote(arg)); }
            var start = new ProcessStartInfo(python, command.ToString());
            start.UseShellExecute = false;
            start.WorkingDirectory = cwd;
            using (var process = Process.Start(start)) { process.WaitForExit(); return process.ExitCode; }
        } catch (Exception ex) { Console.Error.WriteLine("Luople: " + ex.Message); return 1; }
    }
}
