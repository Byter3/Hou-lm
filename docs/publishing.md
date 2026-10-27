# Release checklist

1. Review the intended diff, author and remote. Exclude credentials, HIPs, histories, image assets, diagnostics and generated archives. Preserve `working-2026-10-15`.
2. Run [offline checks](../CONTRIBUTING.md#run-tests) and relevant [native prompts](houdini-panel-quickstart.md#test-auto-run-command-receipts-and-resume). Record skips and untested scope.
3. Set a new version; align changelog and artifact names. Build:

   ```sh
   python scripts/package_houdini.py --output dist/Hou-lm-preview-houdini20.5.zip
   ```

4. Verify archive allowlist/checksums and install from the ZIP with one package registration.
5. Commit and push; check CI, publish the versioned release and attach the plug-in ZIP. Update download links and source-only feature notices.

Keep `dist/` out of Git. Never replace a published version with different contents. Client publication does not deploy the [backend](https://github.com/Byter3/studio-assistant-backend).
