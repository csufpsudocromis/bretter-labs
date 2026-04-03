from src.tables import Image, Template
from src.tools.labinstance_controller import _vm_boot_order_for_launch


def _template(template_id: str) -> Template:
    return Template(
        id=template_id,
        name="Template",
        image_id="img-1",
        cpu_cores=2,
        ram_mb=4096,
    )


def _image(*, source_kind: str, installer_iso_filename: str | None) -> Image:
    return Image(
        id="img-1",
        name="Image 1",
        filename="image1.raw",
        source_kind=source_kind,
        installer_iso_filename=installer_iso_filename,
        checksum="sha256:test",
        size_bytes=1,
    )


def test_boot_order_prefers_iso_for_scratch_images() -> None:
    template = _template("tmpl-1")
    image = _image(source_kind="scratch", installer_iso_filename="installer.iso")

    assert _vm_boot_order_for_launch(template, image) == "dc"


def test_boot_order_prefers_iso_for_image_update_templates() -> None:
    template = _template("img-update-abc123")
    image = _image(source_kind="uploaded", installer_iso_filename="installer.iso")

    assert _vm_boot_order_for_launch(template, image) == "dc"


def test_boot_order_defaults_to_disk_for_uploaded_non_update_templates() -> None:
    template = _template("tmpl-standard")
    image = _image(source_kind="uploaded", installer_iso_filename="installer.iso")

    assert _vm_boot_order_for_launch(template, image) is None
