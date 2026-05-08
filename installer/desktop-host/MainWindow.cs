using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace APICostX.DesktopHost;

internal sealed class MainWindow : Form
{
    private const string DocumentationUrl = "https://github.com/morganross/API-Cost-X#readme";

    private readonly AppPaths _paths;
    private readonly LocalServiceProcess _service;
    private readonly StartupProbe _probe;
    private readonly WebView2 _webView = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly Panel _startupPanel = new() { Dock = DockStyle.Fill, BackColor = Color.FromArgb(248, 250, 252) };
    private readonly Label _statusLabel = new()
    {
        Dock = DockStyle.Fill,
        TextAlign = ContentAlignment.MiddleCenter,
        Font = new Font("Segoe UI", 13F, FontStyle.Regular, GraphicsUnit.Point),
        Padding = new Padding(48)
    };
    private readonly FlowLayoutPanel _errorActions = new()
    {
        AutoSize = true,
        Dock = DockStyle.Top,
        FlowDirection = FlowDirection.LeftToRight,
        Padding = new Padding(0, 0, 0, 36),
        Visible = false,
        WrapContents = false
    };

    private bool _startupAttempted;

    public MainWindow(AppPaths paths, LocalServiceProcess service, StartupProbe probe)
    {
        _paths = paths;
        _service = service;
        _probe = probe;

        Text = "APICostX";
        MinimumSize = new Size(1100, 720);
        Size = new Size(1320, 860);
        StartPosition = FormStartPosition.CenterScreen;

        MainMenuStrip = BuildMenu();
        BuildStartupPanel();
        Controls.Add(_webView);
        Controls.Add(_startupPanel);
        Controls.Add(MainMenuStrip);

        _statusLabel.Text = "Starting APICostX local service...";
    }

    protected override async void OnShown(EventArgs e)
    {
        base.OnShown(e);
        if (_startupAttempted)
        {
            return;
        }

        _startupAttempted = true;
        await StartDesktopShellAsync().ConfigureAwait(true);
    }

    private async Task StartDesktopShellAsync()
    {
        try
        {
            ShowStartupStatus("Starting APICostX local service...");

            if (!_service.IsRunning && !_service.IsLoopbackPortAvailable())
            {
                ShowStartupError($"APICostX cannot start because port {_service.Endpoint.Port} is already in use. Close the other local process or change API_COST_X_API_PORT in {_paths.EnvFile}.");
                return;
            }

            _service.Start();
            ShowStartupStatus("Waiting for APICostX local service...");
            await _probe.WaitUntilReadyAsync(_service.Endpoint, TimeSpan.FromSeconds(60), CancellationToken.None).ConfigureAwait(true);

            ShowStartupStatus("Opening APICostX...");
            await InitializeWebViewAsync().ConfigureAwait(true);
        }
        catch (Exception ex)
        {
            ShowStartupError(ex.Message + Environment.NewLine + Environment.NewLine + "Log file: " + _service.LogPath);
        }
    }

    private async Task InitializeWebViewAsync()
    {
        Directory.CreateDirectory(_paths.WebViewUserDataDirectory);
        CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(
            browserExecutableFolder: null,
            userDataFolder: _paths.WebViewUserDataDirectory).ConfigureAwait(true);

        await _webView.EnsureCoreWebView2Async(environment).ConfigureAwait(true);
        _webView.CoreWebView2.Settings.AreDevToolsEnabled = false;
        _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
        _webView.CoreWebView2.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            if (IsAllowedAppNavigation(args.Uri))
            {
                _webView.CoreWebView2.Navigate(args.Uri);
            }
            else
            {
                OpenExternal(args.Uri);
            }
        };
        _webView.CoreWebView2.NavigationStarting += (_, args) =>
        {
            if (!IsAllowedAppNavigation(args.Uri))
            {
                args.Cancel = true;
                OpenExternal(args.Uri);
            }
        };

        _webView.Source = new Uri(_service.Endpoint.BaseUrl + "/?shell=desktop#/quality");
        _webView.Visible = true;
        _startupPanel.Visible = false;
    }

    private bool IsAllowedAppNavigation(string uriText)
    {
        if (!Uri.TryCreate(uriText, UriKind.Absolute, out Uri? uri))
        {
            return false;
        }

        return string.Equals(uri.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase)
            && string.Equals(uri.Host, _service.Endpoint.Host, StringComparison.Ordinal)
            && uri.Port == _service.Endpoint.Port;
    }

    private static void OpenExternal(string uriText)
    {
        if (!Uri.TryCreate(uriText, UriKind.Absolute, out Uri? uri))
        {
            return;
        }

        if (!string.Equals(uri.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase)
            && !string.Equals(uri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        try
        {
            Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
        }
        catch (InvalidOperationException)
        {
        }
        catch (System.ComponentModel.Win32Exception)
        {
        }
    }

    private MenuStrip BuildMenu()
    {
        var menu = new MenuStrip();
        var file = new ToolStripMenuItem("File");
        file.DropDownItems.Add("Reload", null, (_, _) =>
        {
            if (_webView.CoreWebView2 is not null)
            {
                _webView.Reload();
            }
        });
        file.DropDownItems.Add("Open Data Folder", null, (_, _) => OpenFolder(_paths.DataDirectory));
        file.DropDownItems.Add("Open Logs Folder", null, (_, _) => OpenFolder(_paths.LogsDirectory));
        file.DropDownItems.Add(new ToolStripSeparator());
        file.DropDownItems.Add("Quit", null, (_, _) => Close());

        var help = new ToolStripMenuItem("Help");
        help.DropDownItems.Add("Open Documentation", null, (_, _) => OpenExternal(DocumentationUrl));
        help.DropDownItems.Add("About", null, (_, _) => MessageBox.Show(
            "APICostX desktop shell" + Environment.NewLine + "Local WebView2 host for APICostX.",
            "About APICostX",
            MessageBoxButtons.OK,
            MessageBoxIcon.Information));

        menu.Items.Add(file);
        menu.Items.Add(help);
        return menu;
    }

    private void BuildStartupPanel()
    {
        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 1,
            RowCount = 2
        };
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        _errorActions.Controls.Add(MakeStartupButton("Open Logs Folder", () => OpenFolder(_paths.LogsDirectory)));
        _errorActions.Controls.Add(MakeStartupButton("Retry", async () => await RetryStartupAsync().ConfigureAwait(true)));
        _errorActions.Controls.Add(MakeStartupButton("Quit", Close));

        var actionHost = new Panel { Dock = DockStyle.Fill, AutoSize = true };
        _errorActions.Anchor = AnchorStyles.None;
        actionHost.Controls.Add(_errorActions);

        layout.Controls.Add(_statusLabel, 0, 0);
        layout.Controls.Add(actionHost, 0, 1);
        _startupPanel.Controls.Add(layout);
    }

    private Button MakeStartupButton(string text, Action action)
    {
        var button = new Button
        {
            AutoSize = true,
            Margin = new Padding(8),
            Padding = new Padding(10, 4, 10, 4),
            Text = text
        };
        button.Click += (_, _) => action();
        return button;
    }

    private async Task RetryStartupAsync()
    {
        _startupAttempted = true;
        await StartDesktopShellAsync().ConfigureAwait(true);
    }

    private void ShowStartupStatus(string message)
    {
        _webView.Visible = false;
        _startupPanel.Visible = true;
        _errorActions.Visible = false;
        _statusLabel.Text = message;
    }

    private void ShowStartupError(string message)
    {
        _webView.Visible = false;
        _startupPanel.Visible = true;
        _errorActions.Visible = true;
        _statusLabel.Text = message;
        MessageBox.Show(message, "APICostX startup failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    private static void OpenFolder(string path)
    {
        Directory.CreateDirectory(path);
        Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
    }
}
