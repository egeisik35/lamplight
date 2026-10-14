# Test fixtures

| File | Source |
|------|--------|
| `ite5570.desc` | HID report descriptor read from an ASUS Vivobook S 14 M5406KA (`/sys/class/hidraw/*/device/report_descriptor`) |
| `elan-touchpad.desc` | Touchpad descriptor from the same laptop (non-RGB device) |
| `logitech-m650.desc` | Logitech Signature M650 L mouse descriptor (non-RGB device) |
| `lenovo-loq-ite-c693.desc` | Lenovo LOQ IRX10 keyboard (ITE `048d:c693`), from [Salvo6516/lampctl](https://github.com/Salvo6516/lampctl/tree/main/tests/descriptors) (MIT) |
| `microsoft-sample.desc` | Bytes of `LampArrayReportDescriptor` from [microsoft/ArduinoHidForWindows](https://github.com/microsoft/ArduinoHidForWindows/blob/main/src/LampArrayReportDescriptor.h) |

`microsoft-sample.desc` is covered by this notice:

```
Copyright (c) Microsoft Corporation. All rights reserved.
Licensed under the MIT License.
```

`lenovo-loq-ite-c693.desc` comes from lampctl, released under the MIT License
by its authors.
