#[cfg(test)]
mod cache_reuse_validation {
    use super::*;
    #[test]
    fn initialization_shares_and_reset_preserves_allocation() {
        let dir=std::env::temp_dir().join(format!("qgraph-cache-reuse-{}",std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        struct Cleanup(std::path::PathBuf);
        impl Drop for Cleanup {fn drop(&mut self){let _=fs::remove_dir_all(&self.0);}}
        let _cleanup=Cleanup(dir.clone());
        for name in ["graph.pages","graph_compact.pages","residual.pages"] {
            fs::write(dir.join(name),[0u8;PAGE_SIZE]).unwrap();
        }
        let layout=GraphLayout{node_count:1,max_degree:1,start_point:0,additional_points:0,record_bytes:8,nodes_per_page:512,page_count:1};
        let graph=BackendFactory::direct_with_loaded_cache(&dir.join("graph.pages"),layout.clone(),Arc::new(HashMap::new())).unwrap();
        let cache=graph.shared_query_cache().unwrap();
        let locality=locality::LocalityReader::with_cache(Arc::new(locality::LocalityLayout{dir:dir.clone(),slots:vec![0],graph_bytes:8,compact_bytes:8,residual_bytes:8,max_degree:1}),cache.clone()).unwrap();
        assert!(Arc::ptr_eq(&cache,&locality.cache));
        let ptr=cache.lock().unwrap().0;
        let bytes=cache.lock().unwrap().allocated_bytes();
        for _ in 0..100 {
            cache.lock().unwrap().put(0,7,&[3;PAGE_SIZE]);
            assert_eq!(locality.cache.lock().unwrap().get(0,7).unwrap()[0],3);
            let _backend=graph.create().unwrap();
            assert!(Arc::ptr_eq(&cache,&graph.shared_query_cache().unwrap()));
            let mut c=locality.cache.lock().unwrap();
            assert_eq!(c.0,ptr);assert_eq!(c.allocated_bytes(),bytes);
            assert!(c.get(0,7).is_none());
        }
        let other=BackendFactory::direct_with_loaded_cache(&dir.join("graph.pages"),layout,Arc::new(HashMap::new())).unwrap();
        assert!(!Arc::ptr_eq(&cache,&other.shared_query_cache().unwrap()));
    }
}
