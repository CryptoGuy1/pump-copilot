import { useQuery } from "@tanstack/react-query";
import { call, client } from "../../api/client";

/** The About facts (repository, stack, sources), fetched once. */
export function useAbout() {
  return useQuery({ queryKey: ["about"], staleTime: Infinity,
                    queryFn: () => call(client.GET("/api/about")) });
}

/** A file in the repository (a report), opened on the forge. */
export function RepoFile({ path, children }: { path: string; children?: React.ReactNode }) {
  const repo = useAbout().data?.repository;
  return repo ? <a href={`${repo}/blob/master/${path}`} target="_blank" rel="noreferrer">
    {children ?? path}</a> : <span className="mono">{path}</span>;
}

/** A commit in the repository (a pre-registration or a holdout), by its short hash. */
export function RepoCommit({ sha, label }: { sha: string | null | undefined; label?: string }) {
  const repo = useAbout().data?.repository;
  if (!sha) return null;
  const short = sha.slice(0, 7);
  return repo ? <a href={`${repo}/commit/${sha}`} target="_blank" rel="noreferrer">
    {label ?? "commit"} <span className="mono">{short}</span></a>
    : <span>{label ?? "commit"} <span className="mono">{short}</span></span>;
}
