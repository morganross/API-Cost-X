using System;
using System.Diagnostics;
using System.IO;

namespace APICostXLauncher
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            string appDir = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string workDir = Path.Combine(localAppData, "APICostX");
            Directory.CreateDirectory(workDir);

            string pythonExe = Path.Combine(appDir, "runtime", "python", "python.exe");
            if (!File.Exists(pythonExe))
            {
                Console.Error.WriteLine("Bundled Python runtime was not found: " + pythonExe);
                Console.Error.WriteLine("Reinstall APICostX from the latest installer.");
                return 1;
            }

            string sitePackages = Path.Combine(appDir, "runtime", "python", "Lib", "site-packages");
            string existingPythonPath = Environment.GetEnvironmentVariable("PYTHONPATH") ?? "";
            string pythonPath = string.IsNullOrWhiteSpace(existingPythonPath)
                ? appDir + ";" + sitePackages
                : appDir + ";" + sitePackages + ";" + existingPythonPath;

            var startInfo = new ProcessStartInfo
            {
                FileName = pythonExe,
                WorkingDirectory = workDir,
                UseShellExecute = false,
                Arguments = "-m app.desktop"
            };
            startInfo.Environment["PYTHONPATH"] = pythonPath;
            startInfo.Environment["PYTHONUNBUFFERED"] = "1";
            startInfo.Environment["API_COST_X_INSTALL_DIR"] = appDir;

            using (Process process = Process.Start(startInfo))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
    }
}
