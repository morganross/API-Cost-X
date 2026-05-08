using System;
using System.IO;

namespace APICostX.DesktopHost;

internal sealed record AppPaths(
    string InstallDirectory,
    string LocalAppDataDirectory,
    string DataDirectory,
    string LogsDirectory,
    string EnvFile,
    string RuntimePythonDirectory,
    string PythonExe,
    string SitePackagesDirectory,
    string WebViewUserDataDirectory)
{
    public static AppPaths FromInstallDirectory(string installDirectory)
    {
        if (string.IsNullOrWhiteSpace(installDirectory))
        {
            throw new InvalidOperationException("APICostX install directory could not be determined.");
        }

        string fullInstallDirectory = Path.GetFullPath(installDirectory)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (string.IsNullOrWhiteSpace(localAppData))
        {
            throw new InvalidOperationException("LOCALAPPDATA is not available for the current user.");
        }

        string appData = Path.Combine(localAppData, "APICostX");
        string runtimePython = Path.Combine(fullInstallDirectory, "runtime", "python");

        return new AppPaths(
            InstallDirectory: fullInstallDirectory,
            LocalAppDataDirectory: appData,
            DataDirectory: Path.Combine(appData, "data"),
            LogsDirectory: Path.Combine(appData, "logs"),
            EnvFile: Path.Combine(appData, ".env"),
            RuntimePythonDirectory: runtimePython,
            PythonExe: Path.Combine(runtimePython, "python.exe"),
            SitePackagesDirectory: Path.Combine(runtimePython, "Lib", "site-packages"),
            WebViewUserDataDirectory: Path.Combine(appData, "webview2-profile"));
    }

    public void EnsureWritableDirectories()
    {
        Directory.CreateDirectory(LocalAppDataDirectory);
        Directory.CreateDirectory(DataDirectory);
        Directory.CreateDirectory(LogsDirectory);
        Directory.CreateDirectory(WebViewUserDataDirectory);
    }
}
