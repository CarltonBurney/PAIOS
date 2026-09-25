namespace Paios.CommandCenter.Providers;

/// <summary>
/// One contract for every provider kind, so routes and UI never branch on
/// provider type. Implementations must not throw for network or protocol
/// failures — those are reported as <see cref="HealthState"/> values.
/// </summary>
public interface IModelProviderAdapter
{
    ProviderType ProviderType { get; }

    Task<HealthResult> CheckHealthAsync(string endpoint, CancellationToken cancellationToken = default);

    Task<ModelListResult> ListModelsAsync(string providerId, string endpoint, CancellationToken cancellationToken = default);
}
