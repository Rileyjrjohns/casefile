function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function EvidenceRecord({ data }: { data: unknown }) {
  const record = isObject(data) && isObject(data.record) ? data.record : null;
  if (
    !record ||
    typeof record.title !== "string" ||
    typeof record.issuer !== "string" ||
    typeof record.issued_at !== "string" ||
    typeof record.text !== "string" ||
    !Array.isArray(record.fields) ||
    !record.fields.every(
      (field: unknown) =>
        isObject(field) &&
        typeof field.name === "string" &&
        typeof field.value === "string",
    )
  )
    return <pre>{JSON.stringify(data, null, 2)}</pre>;
  const fields = record.fields as { name: string; value: string }[];
  return (
    <article className="document-evidence">
      <span className="tag">
        {record.verification === "independent"
          ? "INDEPENDENT SOURCE · FICTIONAL"
          : "UNVERIFIED SUBMISSION · FICTIONAL"}
      </span>
      <h3>{record.title}</h3>
      <p className="muted">
        {record.issuer} ·{" "}
        {new Date(record.issued_at).toLocaleDateString("en-GB")}
      </p>
      <dl>
        {fields.map((field) => (
          <div key={field.name}>
            <dt>{field.name.replaceAll("_", " ")}</dt>
            <dd>{field.value}</dd>
          </div>
        ))}
      </dl>
      <p>{record.text}</p>
      <details>
        <summary>Exact source record</summary>
        <pre>{JSON.stringify(data, null, 2)}</pre>
      </details>
    </article>
  );
}
