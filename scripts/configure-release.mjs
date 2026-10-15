import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

// The backend reads the root file; Rollup imports its frontend copy.
// Releases explicitly select their publishing repository. Local builds keep
// project.config.json as the source of truth, including for forks.
export function configureRelease(root, repository) {
  const config = JSON.parse(
    readFileSync(resolve(root, "project.config.json"), "utf8"),
  );
  if (repository !== undefined) {
    if (
      !/^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(repository)
    ) {
      throw new Error("Expected a GitHub repository in owner/repo format");
    }
    [config.githubOwner, config.githubRepo] = repository.split("/");
  }
  const content = JSON.stringify(config, null, 2) + "\n";
  writeFileSync(resolve(root, "project.config.json"), content);
  writeFileSync(resolve(root, "src/project.config.json"), content);
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  configureRelease(process.cwd(), process.argv[2]);
}
