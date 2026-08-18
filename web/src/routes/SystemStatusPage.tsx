import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Database, HardDrive, RefreshCw, Server } from 'lucide-react'
import type { ComponentType } from 'react'

import { API_V1_URL } from '@/api/client'
import { fetchHealth, healthQueryKey, type ComponentHealth, type HealthChecks } from '@/api/health'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

interface CheckDescriptor {
  readonly key: keyof HealthChecks
  readonly label: string
  readonly detail: string
  readonly icon: ComponentType<{ className?: string }>
}

const CHECKS: readonly CheckDescriptor[] = [
  { key: 'database', label: 'Database', detail: 'PostgreSQL 16', icon: Database },
  { key: 'redis', label: 'Redis', detail: 'Background work and caching', icon: Server },
  {
    key: 'object_storage',
    label: 'Object storage',
    detail: 'S3-compatible bucket',
    icon: HardDrive,
  },
]

function formatLatency(latencyMs: number | null): string {
  return latencyMs === null ? '—' : `${latencyMs.toFixed(1)} ms`
}

function CheckCard({
  descriptor,
  health,
}: {
  descriptor: CheckDescriptor
  health: ComponentHealth
}) {
  const Icon = descriptor.icon
  const healthy = health.status === 'ok'

  return (
    <Card data-testid={`check-${descriptor.key}`}>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <Icon className="text-muted-foreground size-4" />
            <CardTitle>{descriptor.label}</CardTitle>
          </div>
          <Badge variant={healthy ? 'success' : 'destructive'} data-testid="check-status">
            {health.status}
          </Badge>
        </div>
        <CardDescription>{descriptor.detail}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        <p className="text-muted-foreground">
          Latency{' '}
          <span className="text-foreground font-medium">{formatLatency(health.latency_ms)}</span>
        </p>
        {health.error ? (
          <p className="text-destructive break-words" data-testid="check-error">
            {health.error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}

export function SystemStatusPage() {
  const { data, error, isFetching, refetch } = useQuery({
    queryKey: healthQueryKey,
    queryFn: ({ signal }) => fetchHealth(signal),
    refetchInterval: 15_000,
  })

  const operational = data?.status === 'ok'

  return (
    <main className="mx-auto flex min-h-svh w-full max-w-3xl flex-col gap-6 px-6 py-12">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">System status</h1>
          <p className="text-muted-foreground text-sm">
            Live report from <code className="text-foreground">{API_V1_URL}/health</code>
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refetch()}
          disabled={isFetching}
          data-testid="refresh"
        >
          <RefreshCw className={isFetching ? 'animate-spin' : undefined} />
          Refresh
        </Button>
      </header>

      {error ? (
        <Card data-testid="health-unreachable">
          <CardHeader>
            <div className="flex items-center gap-2">
              <AlertTriangle className="text-destructive size-4" />
              <CardTitle>API unreachable</CardTitle>
            </div>
            <CardDescription>{error.message}</CardDescription>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm">
            Start the stack with <code className="text-foreground">docker compose up</code>, or
            point <code className="text-foreground">VITE_API_BASE_URL</code> at a running API.
          </CardContent>
        </Card>
      ) : null}

      {data ? (
        <>
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  {operational ? (
                    <CheckCircle2 className="text-success size-5" />
                  ) : (
                    <AlertTriangle className="text-destructive size-5" />
                  )}
                  <CardTitle data-testid="overall-status">
                    {operational ? 'All systems operational' : 'Degraded'}
                  </CardTitle>
                </div>
                <Badge variant={operational ? 'success' : 'destructive'}>{data.status}</Badge>
              </div>
              <CardDescription data-testid="service-identity">
                {data.service} · v{data.version} · {data.environment}
              </CardDescription>
            </CardHeader>
          </Card>

          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {CHECKS.map((descriptor) => (
              <CheckCard
                key={descriptor.key}
                descriptor={descriptor}
                health={data.checks[descriptor.key]}
              />
            ))}
          </section>
        </>
      ) : null}

      {!data && !error ? (
        <p className="text-muted-foreground text-sm" data-testid="health-loading">
          Checking backing services…
        </p>
      ) : null}
    </main>
  )
}
