// The signed-in person's open draft for one repository: edits go to the server as
// they are made, so a reload or another tab sees the same draft.
import { useCallback, useEffect, useState } from "react";
import { api, type ChangeSetInfo, type FileDraftInfo } from "./api/client";
import { decode, encode, type Value } from "./tagged";

export type { ChangeSetInfo, FileDraftInfo };

export type SaveOutcome =
  | { kind: "saved"; files: number; id: string; title: string; paths: string[] }
  | { kind: "conflict"; paths: string[]; message: string }
  | { kind: "error"; message: string };

type RawChange = { op: string; path: (string | number)[]; value?: unknown };

/** Field values a draft sets on one file, by top-level or dotted key. */
export function draftedFields(
  file: FileDraftInfo | undefined,
): Map<string, Value | null> {
  const fields = new Map<string, Value | null>();
  for (const change of (file?.changes ?? []) as RawChange[]) {
    const key = change.path.join(".");
    if (change.op === "set") fields.set(key, decode(change.value));
    if (change.op === "delete") fields.set(key, null);
  }
  return fields;
}

/** A manifest section's list with a draft's changes applied. */
export function draftedList(
  original: string[],
  file: FileDraftInfo | undefined,
  section: string,
): string[] {
  let list = [...original];
  for (const change of (file?.changes ?? []) as RawChange[]) {
    if (change.path.length !== 1 || change.path[0] !== section) continue;
    if (change.op === "set")
      list = (decode(change.value) as Value[]).map(String);
    if (change.op === "append") list.push(String(decode(change.value)));
    if (change.op === "delete") list = [];
  }
  return list;
}

function detailMessage(error: unknown): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail)
    return String((detail as { message: unknown }).message);
  return "The studio refused the change";
}

export function useDraft(repoId: string | undefined, enabled: boolean) {
  const [draft, setDraft] = useState<ChangeSetInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!repoId || !enabled) return;
    const { data } = await api.GET("/api/repositories/{repo_id}/changesets", {
      params: { path: { repo_id: repoId } },
    });
    setDraft(data?.[0] ?? null);
  }, [repoId, enabled]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const ensure = useCallback(async (): Promise<ChangeSetInfo | null> => {
    if (!repoId) return null;
    if (draft) return draft;
    const { data } = await api.POST("/api/repositories/{repo_id}/changesets", {
      params: { path: { repo_id: repoId } },
      body: { title: "Edits from the workspace" },
    });
    if (data) setDraft(data);
    return data ?? null;
  }, [repoId, draft]);

  const editFields = useCallback(
    async (
      path: string,
      base: string,
      fields: Record<string, Value | null>,
    ) => {
      const current = await ensure();
      if (!current || !repoId) return;
      const encoded = Object.fromEntries(
        Object.entries(fields).map(([k, v]) => [
          k,
          v === null ? null : encode(v),
        ]),
      );
      const { data, error: failure } = await api.PATCH(
        "/api/repositories/{repo_id}/changesets/{change_id}/pkginfo/{path}",
        {
          params: { path: { repo_id: repoId, change_id: current.id, path } },
          body: { base_revision: base, fields: encoded },
        },
      );
      if (data) {
        setDraft(data);
        setError(null);
      } else setError(detailMessage(failure));
    },
    [ensure, repoId],
  );

  const editSections = useCallback(
    async (path: string, base: string, sections: Record<string, string[]>) => {
      const current = await ensure();
      if (!current || !repoId) return;
      const { data, error: failure } = await api.PATCH(
        "/api/repositories/{repo_id}/changesets/{change_id}/manifest/{path}",
        {
          params: { path: { repo_id: repoId, change_id: current.id, path } },
          body: { base_revision: base, sections },
        },
      );
      if (data) {
        setDraft(data);
        setError(null);
      } else setError(detailMessage(failure));
    },
    [ensure, repoId],
  );

  /** Re-make one file's edits on top of its current contents. */
  const reapply = useCallback(
    async (path: string) => {
      if (!draft || !repoId) return;
      const file = draft.files.find((f) => f.path === path);
      if (!file?.current_revision) return;
      const { data } = await api.PUT(
        "/api/repositories/{repo_id}/changesets/{change_id}/files/{path}",
        {
          params: { path: { repo_id: repoId, change_id: draft.id, path } },
          body: { base_revision: file.current_revision, changes: file.changes },
        },
      );
      if (data) setDraft(data);
    },
    [draft, repoId],
  );

  /** Drop one file's edits from the draft. */
  const drop = useCallback(
    async (path: string) => {
      if (!draft || !repoId) return;
      const file = draft.files.find((f) => f.path === path);
      const { data } = await api.PUT(
        "/api/repositories/{repo_id}/changesets/{change_id}/files/{path}",
        {
          params: { path: { repo_id: repoId, change_id: draft.id, path } },
          body: { base_revision: file?.base_revision ?? "", changes: [] },
        },
      );
      if (data) setDraft(data);
    },
    [draft, repoId],
  );

  const save = useCallback(
    async (acknowledgeLive: boolean): Promise<SaveOutcome> => {
      if (!draft || !repoId)
        return { kind: "error", message: "Nothing to save" };
      const {
        data,
        error: failure,
        response,
      } = await api.POST(
        "/api/repositories/{repo_id}/changesets/{change_id}/save",
        {
          params: { path: { repo_id: repoId, change_id: draft.id } },
          body: { acknowledge_live: acknowledgeLive },
        },
      );
      if (data) {
        setDraft(null);
        return { kind: "saved", files: data.files.length, id: data.id, title: data.title, paths: data.files.map((f) => f.path) };
      }
      if (response.status === 409) {
        const detail = (failure as { detail?: unknown } | undefined)?.detail;
        const paths = detail && typeof detail === "object" && "paths" in detail ? ((detail as { paths: string[] }).paths ?? []) : [];
        await refresh();
        return { kind: "conflict", paths, message: detailMessage(failure) };
      }
      return { kind: "error", message: detailMessage(failure) };
    },
    [draft, repoId, refresh],
  );

  const discard = useCallback(async () => {
    if (!draft || !repoId) return;
    await api.DELETE("/api/repositories/{repo_id}/changesets/{change_id}", {
      params: { path: { repo_id: repoId, change_id: draft.id } },
    });
    setDraft(null);
  }, [draft, repoId]);

  const fileDraft = (path: string | null) =>
    draft?.files.find((f) => f.path === path);

  return {
    draft,
    error,
    fileDraft,
    editFields,
    editSections,
    reapply,
    drop,
    save,
    discard,
    refresh,
  };
}

export type Draft = ReturnType<typeof useDraft>;
