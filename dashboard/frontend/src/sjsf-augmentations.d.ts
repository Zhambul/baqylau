// sjsf declares its optional fields and labels by module augmentation. The
// strict library check (skipLibCheck: false) reads the core types that name
// them, so the program must include every augmentation.
import '@sjsf/form/extra-fields/aggregated';
import '@sjsf/form/extra-fields/array-item';
import '@sjsf/form/extra-fields/boolean-select';
import '@sjsf/form/extra-fields/enum';
import '@sjsf/form/extra-fields/file';
import '@sjsf/form/extra-fields/files';
import '@sjsf/form/extra-fields/multi-enum';
import '@sjsf/form/extra-fields/native-file';
import '@sjsf/form/extra-fields/native-files';
import '@sjsf/form/extra-fields/object-property';
import '@sjsf/form/extra-fields/remote-enum';
import '@sjsf/form/extra-fields/tags';
import '@sjsf/form/extra-labels/clear';
import '@sjsf/form/extra-labels/edit';
import '@sjsf/form/fields/extra/array-files-include';
import '@sjsf/form/fields/extra/array-native-files-include';
import '@sjsf/form/fields/extra/array-tags-include';
import '@sjsf/form/fields/extra/object-key-enum-include';
import '@sjsf/form/fields/extra/unknown-native-file-include';
