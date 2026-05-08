using System;
using System.Globalization;
using System.IO;

namespace APICostX.DesktopHost;

internal sealed record LocalEndpoint(int Port)
{
    public string Host => "127.0.0.1";
    public string BaseUrl => $"http://127.0.0.1:{Port.ToString(CultureInfo.InvariantCulture)}";
}

internal static class LocalEnvFile
{
    private const string ApiPortKey = "API_COST_X_API_PORT";
    private const int DefaultApiPort = 8000;
    private const int MinUserPort = 1024;
    private const int MaxTcpPort = 65535;

    public static LocalEndpoint ReadEndpoint(string envFile)
    {
        int port = DefaultApiPort;

        if (File.Exists(envFile))
        {
            foreach (string rawLine in File.ReadLines(envFile))
            {
                string line = rawLine.Trim();
                if (line.Length == 0 || line.StartsWith("#", StringComparison.Ordinal))
                {
                    continue;
                }

                int separator = line.IndexOf('=');
                if (separator <= 0)
                {
                    continue;
                }

                string key = line[..separator].Trim();
                if (!string.Equals(key, ApiPortKey, StringComparison.Ordinal))
                {
                    continue;
                }

                string value = line[(separator + 1)..].Trim().Trim('"', '\'');
                if (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out port))
                {
                    throw new InvalidOperationException($"{ApiPortKey} must be an integer between {MinUserPort} and {MaxTcpPort}; got '{value}'.");
                }
            }
        }

        ValidatePort(port);
        return new LocalEndpoint(port);
    }

    private static void ValidatePort(int port)
    {
        if (port < MinUserPort || port > MaxTcpPort)
        {
            throw new InvalidOperationException($"{ApiPortKey} must be between {MinUserPort} and {MaxTcpPort}; got {port}.");
        }
    }
}
