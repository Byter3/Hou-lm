# Release preparation

This guide is for maintainers. Artist instructions are in [Installation](package-install.md).

## Review the source

1. Review the intended branch, `git status --short` and `git diff`. Keep runtime changes separate from documentation-only commits where practical. Preserve the `working-2026-10-15` baseline tag.
2. Check tracked and newly added files for credentials, personal paths, HIPs, image assets, `.houlm` histories, logs, backups and generated archives. Ignore rules do not remove files already tracked.
3. Check the intended author identity and remote before publishing.
4. Run the [offline tests](../CONTRIBUTING.md#run-tests). Record results and skips separately from live Houdini acceptance.
5. Complete the relevant [panel tests](houdini-panel-quickstart.md#test-auto-run-command-receipts-and-resume) and [visual tests](visual-reference.md#example-prompts) on a disposable scene. Do not publish a feature as tested while its acceptance run is still pending.

## Build and inspect the package

Choose the release version before building. Keep the runtime version, changelog, artifact name and release links consistent; do not replace an existing release asset with different contents under the same version.

```sh
python scripts/package_houdini.py --output dist/Hou-lm-preview-houdini20.5.zip
```

The builder refuses to overwrite an existing file. Inspect the ZIP and verify `hou-lm/package-manifest.json` against its entries. The root must contain `hou-lm.json` beside `hou-lm/`. Only the allowlisted client, panel definition, launch/reload helpers, user guides and license belong in it.

Test installation from the ZIP with one active Hou-lm registration. The installed package must work without the source checkout. Keep generated archives in ignored `dist/`; publish the built plug-in ZIP as the release asset, not GitHub's source archive.

## Publish

After the intended source and native acceptance checks are approved, commit the reviewed changes and push to the verified repository. Check the Windows/Linux CI results. Create the versioned release and attach the inspected ZIP, then update the README download link and remove source-only feature notices for features included in that release.

The CI artifact is a build download, not a GitHub release. Publishing a client does not deploy the [shared backend](https://github.com/Byter3/studio-assistant-backend) or change a studio's service configuration.
