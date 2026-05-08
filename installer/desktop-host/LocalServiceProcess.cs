using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;

namespace APICostX.DesktopHost;

internal sealed class LocalServiceProcess : IDisposable
{
    private readonly object _logLock = new();
    private readonly AppPaths _paths;
    private readonly LocalEndpoint _endpoint;
    private Process? _process;
    private StreamWriter? _logWriter;
    private bool _disposed;

    public LocalServiceProcess(AppPaths paths, LocalEndpoint endpoint)
    {
        _paths = paths;
        _endpoint = endpoint;
    }

    public LocalEndpoint Endpoint => _endpoint;

    public string LogPath => Path.Combine(_paths.LogsDirectory, "desktop-service.log");

    public bool IsRunning => _process is not null && !_process.HasExited;

    public void Start()
    {
        ObjectDisposedException.ThrowIf(_disposed, this);

        if (IsRunning)
        {
            return;
        }

        if (!File.Exists(_paths.PythonExe))
        {
            throw new FileNotFoundException("Bundled Python runtime was not found. Reinstall APICostX from the latest installer.", _paths.PythonExe);
        }

        Directory.CreateDirectory(_paths.LogsDirectory);
        OpenLogWriter();
        WriteLog($"[{DateTimeOffset.Now:u}] Starting APICostX local service");

        var startInfo = new ProcessStartInfo
        {
            FileName = _paths.PythonExe,
            Arguments = "-m app.desktop",
            WorkingDirectory = _paths.LocalAppDataDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };

        startInfo.Environment["PYTHONPATH"] = string.Join(Path.PathSeparator, _paths.InstallDirectory, _paths.SitePackagesDirectory);
        startInfo.Environment["PYTHONUNBUFFERED"] = "1";
        startInfo.Environment["API_COST_X_INSTALL_DIR"] = _paths.InstallDirectory;
        startInfo.Environment["API_COST_X_OPEN_BROWSER"] = "0";
        startInfo.Environment["API_COST_X_HOST"] = _endpoint.Host;
        startInfo.Environment["API_COST_X_API_PORT"] = _endpoint.Port.ToString(CultureInfo.InvariantCulture);

        _process = Process.Start(startInfo) ?? throw new InvalidOperationException("Could not start APICostX local service.");
        _process.OutputDataReceived += (_, args) => WriteProcessLine(args.Data);
        _process.ErrorDataReceived += (_, args) => WriteProcessLine(args.Data);
        _process.BeginOutputReadLine();
        _process.BeginErrorReadLine();
    }

    public bool IsLoopbackPortAvailable()
    {
        return IsLoopbackPortAvailable(_endpoint.Port);
    }

    public static bool IsLoopbackPortAvailable(int port)
    {
        try
        {
            using var listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            return true;
        }
        catch (SocketException)
        {
            return false;
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        try
        {
            if (_process is not null && !_process.HasExited)
            {
                _process.Kill(entireProcessTree: true);
                _process.WaitForExit(5000);
            }
        }
        catch (InvalidOperationException)
        {
        }
        finally
        {
            _process?.Dispose();
            _process = null;

            lock (_logLock)
            {
                _logWriter?.Dispose();
                _logWriter = null;
            }
        }
    }

    private void OpenLogWriter()
    {
        lock (_logLock)
        {
            _logWriter?.Dispose();
            _logWriter = new StreamWriter(new FileStream(LogPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
            {
                AutoFlush = true
            };
        }
    }

    private void WriteProcessLine(string? line)
    {
        if (line is not null)
        {
            WriteLog(line);
        }
    }

    private void WriteLog(string line)
    {
        lock (_logLock)
        {
            _logWriter?.WriteLine(line);
        }
    }
}
