use diskann_disk::search::provider::aligned_file_reader::{
    AlignedRead, LinuxAlignedFileReader, traits::AlignedFileReader,
};
use diskann_quantization::alloc::{AlignedAllocator, Poly};

#[test]
fn multiple_batches_and_short_read_recovery() {
    let path = std::env::temp_dir().join(format!("diskann-completions-{}", std::process::id()));
    let data: Vec<u8> = (0..4096 * 300).map(|i| (i / 4096 % 251) as u8).collect();
    std::fs::write(&path, &data).unwrap();
    let mut reader = LinuxAlignedFileReader::new(path.to_str().unwrap()).unwrap();
    let mut bytes = Poly::broadcast(0u8, data.len(), AlignedAllocator::A512).unwrap();
    for _ in 0..4 {
        let mut requests: Vec<_> = bytes.chunks_mut(4096).enumerate()
            .map(|(i, part)| AlignedRead::new((i * 4096) as u64, part).unwrap()).collect();
        reader.read(&mut requests).unwrap();
        drop(requests);
        assert_eq!(&*bytes, &data);
        let mut requests: Vec<_> = bytes.chunks_mut(4096).take(4).enumerate()
            .map(|(i, part)| AlignedRead::new(if i == 0 { data.len() as u64 } else { 0 }, part).unwrap()).collect();
        assert!(reader.read(&mut requests).is_err());
    }
    drop(reader);
    std::fs::remove_file(path).unwrap();
}
