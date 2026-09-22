import type {
  ExtensionViewContext,
  ExtensionWebModule,
} from '@baqylau/extension-api';
import { mount as mountComponent, unmount } from 'svelte';

import View from './View.svelte';

export const mount: ExtensionWebModule['mount'] = (target, context) => {
  const viewState = $state<{ context: ExtensionViewContext }>({ context });
  const instance = mountComponent(View, { target, props: { viewState } });
  return {
    update(next) {
      viewState.context = next;
    },
    async dispose() {
      await unmount(instance);
    },
  };
};
