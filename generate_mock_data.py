import os
import random
import polars as pl

def generate_datasets(f1_size=1000, f2_size=500):
    print("Generating mock datasets...")
    
    # Create mock_data folder in the current directory
    output_dir = "./mock_data"
    os.makedirs(output_dir, exist_ok=True)
    
    # Base names and companies to generate data
    first_names = ["James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas", "Charles",
                   "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
                  "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin"]
    companies = ["Acme", "Apex", "Globex", "Initech", "Umbrella Corp", "Stark Industries", "Wayne Enterprises", 
                 "Hooli", "Veerdy", "Cyberdyne", "Soylent", "Tyrell", "Oscorp", "Vandelay", "Bluth Company"]
    suffixes = ["LLC", "Inc.", "Corp.", "Co.", "Ltd."]
    
    # Deliberate duplicates to create
    deliberate_duplicates = [
        # Same email, different phone formatting, company endings
        {"first_name": "Alice", "last_name": "Smith", "phone_1": "+1 (415) 555-0101", "phone_2": "14155550101", "email_1": "alice.smith@gmail.com", "email_2": "alice.smith+spam@gmail.com", "website_1": "http://www.smithcorp.com", "website_2": "https://smithcorp.com/about", "company_1": "Smith Corp.", "company_2": "Smith Corp LLC"},
        # Same domain (website vs email domain), unique local parts
        {"first_name": "Bob", "last_name": "Jones", "phone_1": "212-555-0202", "phone_2": "(212) 555-0202", "email_1": "bob@acme.com", "email_2": "sales@acme.com", "website_1": "http://acme.com", "website_2": "https://www.acme.com/contact", "company_1": "Acme Inc.", "company_2": "Acme LLC"},
        # Same phone, different emails
        {"first_name": "Charlie", "last_name": "Brown", "phone_1": "650-555-0303", "phone_2": "6505550303", "email_1": "charlie@brown.org", "email_2": "charlie.brown@test.com", "website_1": "http://brown.org", "website_2": "http://brown.org", "company_1": "Brown Co", "company_2": "Brown Co"},
        # Same name, different phone/email
        {"first_name": "David", "last_name": "Miller", "phone_1": "5125550404", "phone_2": "512-555-0999", "email_1": "david.miller@gmail.com", "email_2": "david.miller@work.com", "website_1": "http://miller.net", "website_2": "http://miller.net", "company_1": "Miller LLC", "company_2": "Miller LLC"},
    ]

    # Generate File 1
    f1_rows = []
    # Add duplicate baselines
    for item in deliberate_duplicates:
        f1_rows.append({
            "first_name": item["first_name"],
            "last_name": item["last_name"],
            "phone": item["phone_1"],
            "email": item["email_1"],
            "website": item["website_1"],
            "company": item["company_1"]
        })
        
    for i in range(f1_size - len(deliberate_duplicates)):
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        comp = random.choice(companies) + " " + random.choice(suffixes)
        domain = comp.lower().replace(" ", "").replace(".", "").replace(",", "") + ".com"
        
        # Phone: mix formats and valid/invalid (10% invalid)
        if random.random() < 0.10:
            phone = f"{random.choice([0,1])}{random.randint(10,99)}-555-{random.randint(1000,9999)}"
        else:
            phone = f"+1 ({random.choice([415, 650, 212, 512, 305])}) 555-{random.randint(1000,9999):04d}"
            
        f1_rows.append({
            "first_name": fn,
            "last_name": ln,
            "phone": phone,
            "email": f"{fn.lower()}.{ln.lower()}{random.randint(1,999)}@gmail.com",
            "website": f"https://www.{domain}",
            "company": comp
        })

    # Generate File 2
    f2_rows = []
    # Add duplicate matches
    for item in deliberate_duplicates:
        f2_rows.append({
            "first_name": item["first_name"],
            "last_name": item["last_name"],
            "phone": item["phone_2"],
            "email": item["email_2"],
            "website": item["website_2"],
            "company": item["company_2"]
        })
        
    for i in range(f2_size - len(deliberate_duplicates)):
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        comp = random.choice(companies) + " " + random.choice(suffixes)
        domain = comp.lower().replace(" ", "").replace(".", "").replace(",", "") + ".com"
        
        if random.random() < 0.10:
            phone = f"{random.choice([0,1])}{random.randint(10,99)}-555-{random.randint(1000,9999)}"
        else:
            phone = f"({random.choice([415, 650, 212, 512, 305])}) 555-{random.randint(1000,9999):04d}"
            
        f2_rows.append({
            "first_name": fn,
            "last_name": ln,
            "phone": phone,
            "email": f"{fn.lower()}.{ln.lower()}{random.randint(1000,9999)}@yahoo.com",
            "website": f"https://www.{domain}",
            "company": comp
        })

    # Save to CSV
    df1 = pl.DataFrame(f1_rows)
    df2 = pl.DataFrame(f2_rows)
    
    file1_path = os.path.join(output_dir, "sample_crm_master.csv")
    file2_path = os.path.join(output_dir, "sample_leads_target.csv")
    
    df1.write_csv(file1_path)
    df2.write_csv(file2_path)
    
    print(f"[OK] File 1 created with {df1.height} rows at {file1_path}")
    print(f"[OK] File 2 created with {df2.height} rows at {file2_path}")
    return file1_path, file2_path

if __name__ == "__main__":
    generate_datasets()
