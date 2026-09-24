// Separate correctness-only process: official in-memory search over the exact disk rows.
#define main symphony_port_main
#include "symphonyqg_disk_port.cpp"
#undef main
#include <sys/mman.h>
#include <fcntl.h>

int main(int argc,char** argv) {
    try {
        if(argc!=5 && argc!=7) throw std::runtime_error("usage: reference INDEX QUERY OUTPUT_JSONL TEMP_INDEX");
        const fs::path root=argv[1],output=argv[3],temporary=argv[4];
        if(fs::exists(output)||fs::exists(temporary)) throw std::runtime_error("refusing to overwrite outputs");
        const auto meta=read_meta(root/"index.meta");
        const auto size=fs::file_size(root/"node_rows.pages");
        if(size!=meta.n*meta.pages_per_row*kPage) throw std::runtime_error("invalid row geometry");
        int fd=open((root/"node_rows.pages").c_str(),O_RDONLY);
        if(fd<0) throw std::runtime_error("open failed");
        auto* pages=static_cast<const char*>(mmap(nullptr,size,PROT_READ,MAP_PRIVATE,fd,0));
        if(pages==MAP_FAILED) throw std::runtime_error("mmap failed");
        // Remove only page padding; preserve the official row bytes and rotator exactly.
        {
            std::ofstream native(temporary,std::ios::binary);
            native.write(reinterpret_cast<const char*>(&meta.entry_point),sizeof(meta.entry_point));
            for(std::size_t id=0;id<meta.n;++id)
                native.write(pages+id*meta.pages_per_row*kPage,meta.row_bytes);
            std::ifstream rotator(root/"rotator.bin",std::ios::binary);
            native<<rotator.rdbuf();
            native.flush();if(!native) throw std::runtime_error("reference serialization failed");
        }
        munmap(const_cast<char*>(pages),size);close(fd);
        symqg::QuantizedGraph official(meta.n,meta.degree,meta.dim);
        official.load_index(temporary.c_str());
        auto queries=read_fvecs(argv[2]);
        if(queries.dim!=meta.dim) throw std::runtime_error("query dimension mismatch");
        std::ofstream trace(output);
        std::vector<int> widths;
        std::stringstream list(argc==7?argv[5]:"10,20,40,60,100,160,240,400,580");std::string part;
        while(std::getline(list,part,',')){int w=std::stoi(part);if(w<10)throw std::runtime_error("width < topk");widths.push_back(w);}
        for(int width:widths) {
            official.set_ef(width);
            for(std::size_t q=0;q<queries.rows;++q) {
                std::vector<symqg::PID> ids(kTopK);
                official.search(queries.data.data()+q*queries.dim,kTopK,ids.data());
                trace<<"{\"search_width\":"<<width<<",\"query_id\":"<<q<<",\"result_ids\":[";
                for(int k=0;k<kTopK;++k){if(k)trace<<',';trace<<ids[k];}
                trace<<"]}\n";
            }
        }
        trace.flush();if(!trace) throw std::runtime_error("reference trace write failed");
        fs::remove(temporary);
        std::cout<<"Official reference completed: "<<queries.rows*widths.size()<<" queries\n";
        return 0;
    } catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
