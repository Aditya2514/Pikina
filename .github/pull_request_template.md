## What changed

<!-- One or two sentences. Link the issue if there is one. -->

## How it was tested

- [ ] `pytest` passes
- [ ] Tried it in the desktop app (say which OS)

## Safety checklist

- [ ] New capabilities have a manifest with the right `permission_level`, and a rollback if they change data
- [ ] Nothing writes outside `pikina.paths.data_dir()`
- [ ] OS-specific code lives only in `src/pikina/osbridge/`
