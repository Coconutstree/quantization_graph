use diskann::ANNResult;
use diskann_disk::search::provider::aligned_file_reader::{
    AlignedRead, LinuxAlignedFileReader, A512,
    traits::{AlignedFileReader, AlignedReaderFactory},
};
use crate::query_cache::{ACTIVE, PAGE};

pub struct Factory(pub String);
pub struct Reader(LinuxAlignedFileReader);

impl AlignedReaderFactory for Factory {
    type AlignedReaderType = Reader;
    fn build(&self) -> ANNResult<Reader> {
        Ok(Reader(LinuxAlignedFileReader::new(&self.0)?))
    }
}

impl AlignedFileReader for Reader {
    type Alignment = A512;
    fn read(&mut self, requests: &mut [AlignedRead<u8, A512>]) -> ANNResult<()> {
        ACTIVE.with(|active| {
            let mut active = active.borrow_mut();
            let Some(cache) = active.as_mut() else { return self.0.read(requests); };
            let mut missing = Vec::new();
            for request in requests.iter_mut() {
                let offset = request.offset();
                let bytes = request.aligned_buf_mut();
                if offset % PAGE as u64 != 0 || bytes.len() % PAGE != 0 {
                    // Header or non-page-aligned reads remain direct and are still counted.
                    missing.push(AlignedRead::new(offset, bytes)?);
                    continue;
                }
                for (index, page) in bytes.chunks_mut(PAGE).enumerate() {
                    let page_id = offset / PAGE as u64 + index as u64;
                    if !cache.get(page_id, page) {
                        missing.push(AlignedRead::new(page_id * PAGE as u64, page)?);
                    }
                }
            }
            if !missing.is_empty() {
                self.0.read(&mut missing)?;
                cache.stats.requests += missing.len() as u64;
                for request in &mut missing {
                    let offset = request.offset();
                    let bytes = request.aligned_buf_mut();
                    cache.stats.bytes_read += bytes.len() as u64;
                    if offset % PAGE as u64 == 0 && bytes.len() == PAGE {
                        cache.put(offset / PAGE as u64, bytes);
                    }
                }
            }
            Ok(())
        })
    }
}
