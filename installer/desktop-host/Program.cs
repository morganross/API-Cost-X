using System;
using System.Linq;
using System.Threading;
using System.Windows.Forms;

namespace APICostX.DesktopHost;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        bool selfTest = args.Any(arg => string.Equals(arg, "--self-test", StringComparison.OrdinalIgnoreCase));
        AppPaths paths;
        try
        {
            paths = AppPaths.FromInstallDirectory(AppContext.BaseDirectory);
        }
        catch (Exception ex)
        {
            return selfTest ? WriteSelfTestFailure(ex) : ShowStartupFailure(ex);
        }

        if (selfTest)
        {
            try
            {
                return HostSelfTest.Run(paths, Console.Out, Console.Error);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(ex.Message);
                return 1;
            }
        }

        try
        {
            using var mutex = new Mutex(initiallyOwned: true, name: "Local\\APICostXDesktopShell", out bool ownsMutex);
            ApplicationConfiguration.Initialize();

            if (!ownsMutex)
            {
                MessageBox.Show("APICostX is already running.", "APICostX", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 0;
            }

            paths.EnsureWritableDirectories();
            LocalEndpoint endpoint = LocalEnvFile.ReadEndpoint(paths.EnvFile);
            using var service = new LocalServiceProcess(paths, endpoint);
            using var window = new MainWindow(paths, service, new StartupProbe());
            Application.Run(window);
            return 0;
        }
        catch (Exception ex)
        {
            return ShowStartupFailure(ex);
        }
    }

    private static int WriteSelfTestFailure(Exception ex)
    {
        Console.Error.WriteLine(ex.Message);
        return 1;
    }

    private static int ShowStartupFailure(Exception ex)
    {
        try
        {
            MessageBox.Show(ex.Message, "APICostX startup failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        catch
        {
            Console.Error.WriteLine(ex.Message);
        }

        return 1;
    }
}
