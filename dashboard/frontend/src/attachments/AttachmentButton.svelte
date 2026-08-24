<script lang="ts">
  let {
    disabled = false,
    onpick,
  }: {
    disabled?: boolean;
    onpick: (files: FileList) => void;
  } = $props();

  let picker = $state<HTMLInputElement>();

  function pick(): void {
    if (!disabled) picker?.click();
  }

  function picked(): void {
    if (picker?.files !== null && picker?.files !== undefined)
      onpick(picker.files);
    if (picker !== undefined) picker.value = '';
  }
</script>

<button
  class="cattach"
  type="button"
  title="attach image or file"
  {disabled}
  onclick={pick}
>
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path
      d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"
    ></path>
  </svg>
</button>
<input
  bind:this={picker}
  class="attach-input"
  type="file"
  multiple
  onchange={picked}
/>
