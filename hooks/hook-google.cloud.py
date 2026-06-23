# Override the _pyinstaller_hooks_contrib hook that crashes when
# google-cloud-core isn't installed. Ubiquity doesn't use google.cloud.
datas = []
hiddenimports = []
