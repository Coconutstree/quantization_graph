use diskann::{ANNError, ANNResult};
use diskann_providers::storage::{StorageReadProvider, StorageWriteProvider};
use diskann_providers::model::IndexConfiguration;

use crate::build::disk_index_build_parameter::DiskIndexBuildParameters;
use crate::storage::DiskIndexWriter;

pub struct DiskIndexBuilder<Data, StorageProvider> {
    _marker: std::marker::PhantomData<(Data, StorageProvider)>,
}

impl<Data, StorageProvider> DiskIndexBuilder<Data, StorageProvider>
where
    StorageProvider: StorageReadProvider + StorageWriteProvider,
{
    pub fn new(
        _storage_provider: &StorageProvider,
        _build_parameters: DiskIndexBuildParameters,
        _index_configuration: IndexConfiguration,
        _disk_index_writer: DiskIndexWriter,
    ) -> ANNResult<Self> {
        Ok(Self {
            _marker: std::marker::PhantomData,
        })
    }

    pub fn build(&mut self) -> ANNResult<()> {
        Err(ANNError::message("disk index builder module is unavailable in this source snapshot"))
    }
}
