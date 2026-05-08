using System;
using System.IO;
using System.Threading;

namespace APICostX.DesktopHost;

internal static class HostSelfTest
{
    public static int Run(AppPaths paths, TextWriter output, TextWriter error)
    {
        paths.EnsureWritableDirectories();

        string[] requiredFiles =
        {
            paths.PythonExe,
            Path.Combine(paths.InstallDirectory, ".env.example"),
            Path.Combine(paths.InstallDirectory, "assets", "react-build", "index.html")
        };

        foreach (string path in requiredFiles)
        {
            if (!File.Exists(path))
            {
                error.WriteLine($"Missing required desktop host dependency: {path}");
                return 1;
            }
        }

        try
        {
            LocalEndpoint endpoint = LocalEnvFile.ReadEndpoint(paths.EnvFile);
            if (!LocalServiceProcess.IsLoopbackPortAvailable(endpoint.Port))
            {
                error.WriteLine($"Cannot run desktop-host self-test because 127.0.0.1:{endpoint.Port} is already in use.");
                return 1;
            }

            using var service = new LocalServiceProcess(paths, endpoint);
            service.Start();
            new StartupProbe().WaitUntilReadyAsync(endpoint, TimeSpan.FromSeconds(60), CancellationToken.None)
                .GetAwaiter()
                .GetResult();

            output.WriteLine($"desktop-host self-test passed for {endpoint.BaseUrl}");
            return 0;
        }
        catch (Exception ex)
        {
            error.WriteLine(ex.Message);
            return 1;
        }
    }
}
