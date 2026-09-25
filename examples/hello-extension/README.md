# Hello example extension

A small external package that follows `docs/extensions/authoring.md`. It adds a
feed card for each finished turn of a session, answers the query
`example.hello.greeting`, draws the terminal view `example.hello.status`, and
mounts the web view `example.hello.page` on a workspace page.

Build its environment (see the guide), then run its case:

```sh
BAQYLAU_HOST_EXECUTABLE=/path/to/bin/baqylau-dashboard \
  python -m baqylau_extension_testkit.runner examples/hello-extension
```

The host repository runs this in `tests/extension_testkit/test_example_package.py`.
