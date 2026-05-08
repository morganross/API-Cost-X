using System;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace APICostX.DesktopHost;

internal sealed class StartupProbe
{
    private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(250);
    private readonly HttpClient _client = new() { Timeout = TimeSpan.FromSeconds(1) };

    public async Task WaitUntilReadyAsync(LocalEndpoint endpoint, TimeSpan timeout, CancellationToken cancellationToken)
    {
        if (timeout <= TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(nameof(timeout), timeout, "Startup probe timeout must be positive.");
        }

        Uri healthUri = new($"{endpoint.BaseUrl}/api/health");
        DateTimeOffset deadline = DateTimeOffset.UtcNow.Add(timeout);
        Exception? lastFailure = null;

        while (DateTimeOffset.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();

            try
            {
                using HttpResponseMessage response = await _client.GetAsync(healthUri, cancellationToken).ConfigureAwait(true);
                if (response.IsSuccessStatusCode)
                {
                    return;
                }

                lastFailure = new HttpRequestException($"Health probe returned {(int)response.StatusCode} {response.ReasonPhrase}.");
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (HttpRequestException ex)
            {
                lastFailure = ex;
            }
            catch (TaskCanceledException ex)
            {
                lastFailure = ex;
            }

            TimeSpan remaining = deadline - DateTimeOffset.UtcNow;
            if (remaining <= TimeSpan.Zero)
            {
                break;
            }

            TimeSpan delay = remaining < PollInterval ? remaining : PollInterval;
            await Task.Delay(delay, cancellationToken).ConfigureAwait(true);
        }

        throw CreateTimeoutException(healthUri, timeout, lastFailure);
    }

    private static TimeoutException CreateTimeoutException(Uri healthUri, TimeSpan timeout, Exception? innerException)
    {
        string message = $"APICostX did not become ready at {healthUri} within {timeout.TotalSeconds:0} seconds.";
        return innerException is null ? new TimeoutException(message) : new TimeoutException(message, innerException);
    }
}
