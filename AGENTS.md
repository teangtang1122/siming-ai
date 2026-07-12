# Siming Release Requirement

For every user-visible feature change in Siming, complete the release workflow
before reporting the work as finished unless the user explicitly asks not to
release it:

1. Update the application version and release metadata.
2. Run the relevant backend tests and `frontend/npm run build`.
3. Build the distributable with `build-exe.bat` and verify `Siming.exe`,
   `update.json`, and `sha256.txt` agree.
4. Commit and push the versioned change.
5. Create or update the corresponding GitHub Release and upload the verified
   release assets.

Report the release URL, commit, and validation outcome in the final handoff.
