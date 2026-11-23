import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { ErrorNotice, LoadingNotice } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getHardware, refreshHardware } from "@/service/commands";
import type { HardwareInfo } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";
import { formatBytes } from "./format";

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-sm font-medium">{label}</dt>
      <dd className="text-sm text-muted-foreground">{value}</dd>
    </div>
  );
}

/** One detected GPU: name if known, vendor always; VRAM only when known. */
function GpuLine({ gpu }: { gpu: HardwareInfo["gpus"][number] }) {
  const name = gpu.name ?? gpu.vendor;
  const vram = gpu.vram_bytes !== null ? ` · ${formatBytes(gpu.vram_bytes)} VRAM` : " · VRAM unknown";
  return <li>{`${name}${vram}`}</li>;
}

/**
 * The machine-facts snapshot this computer's recommendations are computed
 * from (S6-02's closed `/hardware` shape). All local: detection runs the
 * platform's own bounded probes and never touches the network. `gpus: []`
 * is an honest empty state (CPU-only), not an error.
 */
export function HardwareSummaryCard({ enabled }: { enabled: boolean }) {
  const hardware = useServiceQuery(getHardware, "hardware", enabled);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<unknown>(null);

  async function redetect() {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshError(null);
    try {
      await refreshHardware();
      hardware.reload();
    } catch (err) {
      setRefreshError(err);
    } finally {
      setRefreshing(false);
    }
  }

  const info = hardware.data;

  return (
    <Card>
      <CardHeader className="justify-items-start gap-3">
        <CardTitle>This computer</CardTitle>
        <CardDescription className="max-w-prose">
          Recommendations compare the model catalog against this hardware snapshot. Detection is
          local and never touches the network.
        </CardDescription>
        {hardware.loading && info === null && <LoadingNotice label="Detecting hardware…" />}
        {hardware.error !== null && info === null && (
          <ErrorNotice title="Hardware could not be detected" error={hardware.error} onRetry={hardware.reload} />
        )}
        {refreshError !== null && (
          <ErrorNotice title="Hardware could not be re-detected" error={refreshError} onRetry={redetect} />
        )}
        {info !== null && (
          <>
            <dl className="grid gap-3 sm:grid-cols-2">
              <Detail
                label="Operating system"
                value={info.os_version !== null ? `${info.os_name} ${info.os_version}` : info.os_name}
              />
              <Detail
                label="Processor"
                value={info.cpu_model ?? "Not detected"}
              />
              <Detail
                label="Memory"
                value={info.ram_total_bytes !== null ? `${formatBytes(info.ram_total_bytes)} total` : "Not detected"}
              />
              <Detail
                label="Storage"
                value={
                  info.model_dir_free_bytes !== null
                    ? `${formatBytes(info.model_dir_free_bytes)} free for model files`
                    : "Not detected"
                }
              />
              <div className="flex flex-col gap-0.5 sm:col-span-2">
                <dt className="text-sm font-medium">Graphics</dt>
                {info.gpus.length === 0 ? (
                  <dd className="text-sm text-muted-foreground">
                    No GPU detected — CPU-only recommendations
                  </dd>
                ) : (
                  <dd>
                    <ul className="list-disc pl-5 text-sm text-muted-foreground">
                      {info.gpus.map((gpu, index) => (
                        <GpuLine key={index} gpu={gpu} />
                      ))}
                    </ul>
                  </dd>
                )}
              </div>
            </dl>
            {!info.detection_complete && (
              <p className="text-sm text-muted-foreground" role="note">
                Some details could not be detected on this computer. Recommendations that need them
                will say so instead of guessing.
              </p>
            )}
            <div>
              <Button
                type="button"
                variant="outline"
                onClick={redetect}
                aria-disabled={refreshing || undefined}
              >
                <RefreshCw aria-hidden="true" className="size-4" />
                {refreshing ? "Re-detecting…" : "Re-detect hardware"}
              </Button>
            </div>
          </>
        )}
      </CardHeader>
    </Card>
  );
}
