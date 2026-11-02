import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, Info } from "lucide-react";
import { ErrorNotice, LoadingNotice, ScreenHeading, ServiceNotReady } from "@/components/screen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { createAssessment, listFrameworks } from "@/service/commands";
import { asServiceCallError, fieldErrorsOf } from "@/service/errors";
import type { Assessment, SupervisorState } from "@/service/types";
import { useServiceQuery } from "@/service/useServiceQuery";

// Same limits the service enforces (`service/README.md` "Endpoints").
export const NAME_MAX = 200;
export const DESCRIPTION_MAX = 2000;

type Field = "name" | "description" | "framework_id";
type FieldErrors = Partial<Record<Field, string>>;

function validate(name: string, description: string, frameworkId: string): FieldErrors {
  const errors: FieldErrors = {};
  const trimmed = name.trim();
  if (!trimmed) errors.name = "Enter a name for the assessment.";
  else if (trimmed.length > NAME_MAX) errors.name = `Use ${NAME_MAX} characters or fewer.`;
  if (description.trim().length > DESCRIPTION_MAX)
    errors.description = `Use ${DESCRIPTION_MAX} characters or fewer.`;
  if (!frameworkId) errors.framework_id = "Choose a framework.";
  return errors;
}

/**
 * Creates an assessment: a name, an optional description, and the framework
 * it targets. Baseline/subset scoping and cloning are deferred (owner
 * decision PD-006), so they are not offered here.
 */
export function NewAssessmentScreen({
  status,
  focusOnMount,
  onCancel,
  onCreated,
}: {
  status: SupervisorState;
  focusOnMount: boolean;
  onCancel: () => void;
  onCreated: (assessment: Assessment) => void;
}) {
  const ready = status.state === "ready";
  const frameworks = useServiceQuery(listFrameworks, "frameworks", ready);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [frameworkId, setFrameworkId] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const ids = { name: useId(), description: useId(), framework: useId() };
  const refs = {
    name: useRef<HTMLInputElement>(null),
    description: useRef<HTMLTextAreaElement>(null),
    framework_id: useRef<HTMLSelectElement>(null),
  };

  // Preselect when exactly one framework exists (V0.1: NIST SP 800-53 Rev. 5).
  useEffect(() => {
    const list = frameworks.data;
    if (list && list.length > 0 && !list.some((f) => f.id === frameworkId)) setFrameworkId(list[0].id);
  }, [frameworks.data, frameworkId]);

  function focusFirstInvalid(found: FieldErrors) {
    const first = (["name", "description", "framework_id"] as const).find((f) => found[f]);
    if (first) refs[first].current?.focus();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    const found = validate(name, description, frameworkId);
    setErrors(found);
    setSubmitError(null);
    if (Object.keys(found).length > 0) {
      focusFirstInvalid(found);
      return;
    }
    setSubmitting(true);
    try {
      const created = await createAssessment({
        name: name.trim(),
        description: description.trim() || null,
        frameworkId,
      });
      onCreated(created);
    } catch (err) {
      const fromService: FieldErrors = {};
      for (const [field, detail] of Object.entries(fieldErrorsOf(err))) {
        if (field === "name" || field === "description" || field === "framework_id") {
          fromService[field] = detail.message;
        }
      }
      setErrors(fromService);
      const e = asServiceCallError(err);
      // Field-level problems are shown on the fields; anything else as an alert.
      if (Object.keys(fromService).length === 0 || e?.kind !== "api_error") setSubmitError(err);
      focusFirstInvalid(fromService);
      setSubmitting(false);
    }
  }

  const selected = frameworks.data?.find((f) => f.id === frameworkId);
  const describedBy = (field: Field, hintId: string) =>
    [hintId, errors[field] ? `${hintId}-error` : null].filter(Boolean).join(" ");

  return (
    <div className="flex flex-col gap-8">
      <div>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} className="-ml-2.5">
          <ArrowLeft aria-hidden="true" />
          Back to assessments
        </Button>
      </div>
      <header className="flex flex-col gap-2">
        <ScreenHeading focusOnMount={focusOnMount}>New assessment</ScreenHeading>
        <p className="max-w-prose text-muted-foreground">
          Name the assessment and choose the framework it covers. You can add evidence in a later
          release.
        </p>
      </header>

      {!ready ? (
        <ServiceNotReady status={status} />
      ) : frameworks.loading && !frameworks.data ? (
        <LoadingNotice label="Loading frameworks…" />
      ) : frameworks.error ? (
        <ErrorNotice title="Frameworks could not be loaded" error={frameworks.error} onRetry={frameworks.reload} />
      ) : !frameworks.data || frameworks.data.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>No framework is available</CardTitle>
            <CardDescription className="max-w-prose">
              An assessment needs a framework, and none is installed. Reinstall ControlGauge to
              restore the bundled framework information.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <Card>
          <CardContent>
            <form noValidate onSubmit={submit} className="flex flex-col gap-6" aria-label="New assessment">
              {submitError !== null && <ErrorNotice title="The assessment was not created" error={submitError} />}

              <div className="flex flex-col gap-2">
                <Label htmlFor={ids.name}>
                  Name <span aria-hidden="true" className="text-destructive">*</span>
                </Label>
                <Input
                  id={ids.name}
                  ref={refs.name}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={NAME_MAX}
                  required
                  autoComplete="off"
                  aria-invalid={errors.name ? true : undefined}
                  aria-describedby={describedBy("name", `${ids.name}-hint`)}
                />
                <p id={`${ids.name}-hint`} className="text-xs text-muted-foreground">
                  Required. Up to {NAME_MAX} characters, for example “FY26 annual assessment”.
                </p>
                {errors.name && (
                  <p id={`${ids.name}-hint-error`} className="text-sm text-destructive">
                    {errors.name}
                  </p>
                )}
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor={ids.description}>Description</Label>
                <Textarea
                  id={ids.description}
                  ref={refs.description}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  maxLength={DESCRIPTION_MAX}
                  rows={4}
                  aria-invalid={errors.description ? true : undefined}
                  aria-describedby={describedBy("description", `${ids.description}-hint`)}
                />
                <p id={`${ids.description}-hint`} className="text-xs text-muted-foreground">
                  Optional. Scope, system, or period covered ({description.length}/{DESCRIPTION_MAX}).
                </p>
                {errors.description && (
                  <p id={`${ids.description}-hint-error`} className="text-sm text-destructive">
                    {errors.description}
                  </p>
                )}
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor={ids.framework}>Framework</Label>
                <NativeSelect
                  className="w-full"
                  id={ids.framework}
                  ref={refs.framework_id}
                  value={frameworkId}
                  onChange={(e) => setFrameworkId(e.target.value)}
                  aria-invalid={errors.framework_id ? true : undefined}
                  aria-describedby={describedBy("framework_id", `${ids.framework}-hint`)}
                >
                  {frameworks.data.map((f) => (
                    <NativeSelectOption key={f.id} value={f.id}>
                      {f.name} {f.version}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
                <p id={`${ids.framework}-hint`} className="flex gap-1.5 text-xs text-muted-foreground">
                  <Info aria-hidden="true" className="mt-px size-3.5 shrink-0" />
                  {selected && selected.control_count === 0
                    ? "This framework's control catalog is not loaded yet; it will be added in a later release. Every control in the framework will be in scope."
                    : "Every control in the framework will be in scope."}
                </p>
                {errors.framework_id && (
                  <p id={`${ids.framework}-hint-error`} className="text-sm text-destructive">
                    {errors.framework_id}
                  </p>
                )}
              </div>

              <div className="flex flex-wrap gap-2">
                <Button type="submit" aria-disabled={submitting || undefined}>
                  {submitting ? "Creating…" : "Create assessment"}
                </Button>
                <Button type="button" variant="outline" onClick={onCancel}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
