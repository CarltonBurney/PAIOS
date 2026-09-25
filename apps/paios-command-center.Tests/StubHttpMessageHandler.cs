using System.Net;
using System.Text;

namespace Paios.CommandCenter.Tests;

/// <summary>
/// Serves canned responses (or throws canned transport failures) so adapter
/// behaviour can be asserted without depending on a live model server.
/// </summary>
internal sealed class StubHttpMessageHandler(Func<HttpRequestMessage, HttpResponseMessage> responder)
    : HttpMessageHandler
{
    public List<HttpRequestMessage> Requests { get; } = new();

    /// <summary>
    /// Request bodies, captured at send time. A caller that disposes its request
    /// after sending — as it should — leaves the content unreadable afterwards, so
    /// asserting on what was sent requires reading it here.
    /// </summary>
    public List<string> Bodies { get; } = new();

    public static StubHttpMessageHandler Returning(HttpStatusCode status, string body, string contentType = "application/json")
        => new(_ => new HttpResponseMessage(status)
        {
            Content = new StringContent(body, Encoding.UTF8, contentType)
        });

    public static StubHttpMessageHandler Throwing(Exception exception)
        => new(_ => throw exception);

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Requests.Add(request);
        Bodies.Add(request.Content is null
            ? string.Empty
            : await request.Content.ReadAsStringAsync(cancellationToken));

        return responder(request);
    }
}
