<script lang="ts">
  import type { EscapedHtml } from './markup';

  let { html }: { html: EscapedHtml } = $props();
</script>

<!-- eslint-disable-next-line svelte/no-at-html-tags -- EscapedHtml can only be created by the escaping module. -->
{@html html.value}
