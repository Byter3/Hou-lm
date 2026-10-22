# Publishing Hou-lm

The initial source checkpoint is marked `working-2026-10-15`. It includes the MIT license and public documentation. Runtime behavior is recorded in [the baseline guide](working-baseline.md).

## Before publishing source

1. Review `git status --short` and `git ls-files`. The published tree must exclude `.env`, credential exports, HIPs, `.houlm` histories, logs, screenshots, local backups and generated `dist/` archives.
2. Check `git log --format=fuller -1` for the intended author identity. Public commits expose that metadata.
3. Run the tests in [CONTRIBUTING](../CONTRIBUTING.md#run-tests). Record skips and distinguish local checks from hosted CI and live Houdini acceptance.
4. For the first push only, create an empty GitHub repository with the desired visibility. Do not initialize a separate README or license; this checkout already contains them.

## Push when ready

Replace `OWNER/REPOSITORY` with the repository you created. From this checkout:

```sh
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin HEAD:main
git push origin working-2026-10-15
```

If `origin` already exists, inspect `git remote -v` and use the intended remote instead of adding another one. The first command only configures the remote; the push commands publish the source and baseline tag.

After pushing, verify the public file list and run the configured GitHub Actions workflow. Its Windows/Linux Python jobs skip unavailable Houdini Qt and platform-specific checks; they do not replace interactive validation. Gateway and LiteLLM adapter checks belong to the backend repository. Enable private vulnerability reporting if desired so the channel described in `SECURITY.md` is available.

Describe the project as an early Windows/Houdini 20.5.445 release. Do not attach private diagnostic archives or claim that the client release establishes a reproducible server deployment. The optional server example is maintained separately in [studio-assistant-backend](https://github.com/Byter3/studio-assistant-backend).

## Build the artist plug-in archive

Run `python scripts/package_houdini.py` from the reviewed checkout. The default output is `dist/Hou-lm-0.1.0-houdini20.5.zip`; `--output /path/to/package.zip` selects another output file. Check the archive entries and its `hou-lm/package-manifest.json` before attaching that ZIP to an authorized GitHub release.

Alternatively, the **Tests** workflow is configured to build the same ZIP after its Windows and Linux test jobs pass. Download it from the successful run's **Artifacts** section. The upload retains the installable ZIP directly, without an outer archive. Verify the run and source revision; the configured job is not evidence that a hosted run has succeeded. Artifact upload does not publish a release, which remains a separate authorized action.

The ZIP must contain `hou-lm.json` and the sibling `hou-lm` folder directly at its root. It contains the allowlisted client runtime, named panel definition, launch/reload helpers, installation guide and MIT license. Keep credentials, preferences, HIPs, `.houlm`, test fixtures, backups, server configuration and diagnostics out of this artifact. The installed client must not require the source checkout.

Use [the package guide](package-install.md) for artist instructions and its read-only acceptance prompt. A successful archive build or offline suite does not establish interactive package discovery; record that check separately. Do not claim Houdini 21 or Qt6 compatibility from the current Houdini 20.5 package.

GitHub's automatic source ZIP is a checkout, not the ready-to-copy plug-in archive. Publish the built ZIP as a release asset when release publication is authorized; keep `dist/` ignored in Git. Build the optional gateway source bundle from the [backend repository](https://github.com/Byter3/studio-assistant-backend) using its packaging instructions. Do not attach a backend artifact to a client release as though it were required for installation.
