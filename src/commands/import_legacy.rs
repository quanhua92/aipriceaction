use crate::services;
use std::path::PathBuf;

pub fn run(source: Option<PathBuf>, force: bool) {
    let source_path = source.unwrap_or_else(|| PathBuf::from("./references/aipriceaction-data"));

    println!("📁 Source path: {}", source_path.display());
    if force {
        println!("⚠️  Force mode: existing files will be deleted and reimported");
    }

    if !source_path.exists() {
        eprintln!("❌ Error: Source path does not exist: {}", source_path.display());
        eprintln!("   Please provide a valid path to the aipriceaction-data directory.");
        std::process::exit(1);
    }

    match services::import_legacy(&source_path, force) {
        Ok(()) => {
            println!("\n🎉 Import completed successfully!");
        }
        Err(e) => {
            eprintln!("\n❌ Import failed: {}", e);
            std::process::exit(1);
        }
    }
}
