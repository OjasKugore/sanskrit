export default function ({parentElement, setTriggerValue}) {
  const status = parentElement.querySelector("[role=status]");
  const paste = (event) => {
    const file = Array.from(event.clipboardData?.items || [])
      .find(item => item.kind === "file" && item.type.startsWith("image/"))?.getAsFile();
    if (!file) return;
    event.preventDefault();
    if (file.size > 30 * 1024 * 1024) {
      status.textContent = "Choose an image smaller than 30 MB.";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => setTriggerValue("image", {name:file.name, data:reader.result.split(",")[1]});
    reader.onerror = () => { status.textContent = "Could not read this clipboard image."; };
    reader.readAsDataURL(file);
  };
  document.addEventListener("paste", paste);
  return () => document.removeEventListener("paste", paste);
}
