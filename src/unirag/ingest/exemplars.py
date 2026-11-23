"""Labelled example questions for the semantic router (stage 1).

Routes
  sql          answerable from the database alone (counts, amounts, dates, lists, personal records)
  docs         answerable from policy documents alone (rules, procedures, definitions)
  hybrid       needs a rule from a document AND live values from the database
  out_of_scope not about this university's academics, fees, grants, hostel or timetable

These are kept separate from the evaluation questions (eval/questions.jsonl) so that router
accuracy is not measured on examples it was built from.
"""
EXEMPLARS: list[tuple[str, str]] = [
    # ---- sql (16)
    ("How many students are enrolled in the IT department?", "sql"),
    ("List all open research grants with their deadlines.", "sql"),
    ("What is the total amount of overdue fees across all students?", "sql"),
    ("Which courses does Prof. A. Phadke teach this term?", "sql"),
    ("When is the Machine Learning elective scheduled during the week?", "sql"),
    ("How many seats are still free in the Cloud Computing elective?", "sql"),
    ("What is the average CGPA of third-year ENTC students?", "sql"),
    ("Show my pending fee payments.", "sql"),
    ("What is the tuition fee for second-year CS students?", "sql"),
    ("How many students have applied for GR-AI-01?", "sql"),
    ("Which room is the Compiler Design lecture in on Monday?", "sql"),
    ("Who is the head of the ENTC department?", "sql"),
    ("List students with attendance below 65 percent in any course.", "sql"),
    ("What is the amount of the Sustainability & Green Computing Grant?", "sql"),
    ("How many hostellers are there in the fourth year?", "sql"),
    ("Which grants are upcoming and when do they open?", "sql"),
    # ---- docs (16)
    ("What is the minimum attendance required to sit for end-semester exams?", "docs"),
    ("How is late fine calculated on overdue fees?", "docs"),
    ("What documents do I need to apply for the Cybersecurity Research Scholarship?", "docs"),
    ("What is the refund policy if I withdraw within two weeks of classes starting?", "docs"),
    ("How do I convert my CGPA to a 4-point GPA?", "docs"),
    ("What are the hostel gate timings?", "docs"),
    ("Can I hold a research grant and a merit concession together?", "docs"),
    ("What happens if my attendance is below 65 percent?", "docs"),
    ("How are research grant applications selected?", "docs"),
    ("What is the credit overload rule?", "docs"),
    ("When is the hostel renewal window?", "docs"),
    ("What are the merit concession slabs?", "docs"),
    ("Who is eligible for the VLSI Design Research Fellowship?", "docs"),
    ("How do I raise a grievance about my grades?", "docs"),
    ("Is the laboratory fee refundable after the term starts?", "docs"),
    ("What is the academic probation rule?", "docs"),
    # ---- hybrid (18)
    ("I am a second-year IT student with CGPA 8.2. Which grants can I apply for and by when?", "hybrid"),
    ("Am I eligible for a merit fee concession and how much would I save?", "hybrid"),
    ("Can I renew my hostel room next year given my current fee status?", "hybrid"),
    ("How much late fine do I owe on my overdue fees as of today?", "hybrid"),
    ("Which of my courses would block my hall ticket because of attendance?", "hybrid"),
    ("Can I add one more elective this term under the credit rules?", "hybrid"),
    ("Which final-year ENTC grants are open and what are the amounts?", "hybrid"),
    ("With a 3.5 GPA as a third-year IT student, what research funding is available and what is the deadline?", "hybrid"),
    ("How many CS students currently meet the CGPA requirement for the AI for Social Good fellowship?", "hybrid"),
    ("If I withdraw now, how much of the tuition I paid will be refunded?", "hybrid"),
    ("Which students are eligible for the 25 percent merit concession?", "hybrid"),
    ("Do I qualify for the Cybersecurity scholarship given my dues?", "hybrid"),
    ("How many students would lose hostel renewal because of overdue fees?", "hybrid"),
    ("Which grants am I eligible for that close before January?", "hybrid"),
    ("Is my attendance enough to take all my exams, and is my exam fee paid?", "hybrid"),
    ("What is the maximum number of credits I can take, and how many have I registered?", "hybrid"),
    ("What concession would a CS third-year with CGPA 9.1 get in rupees?", "hybrid"),
    ("List the grants a fourth-year student with CGPA 8.0 in ENTC can still apply for.", "hybrid"),
    # ---- out_of_scope (10)
    ("What is the weather in Pune tomorrow?", "out_of_scope"),
    ("Write me a poem about engineering.", "out_of_scope"),
    ("Who won the cricket match yesterday?", "out_of_scope"),
    ("What is the admission cutoff at IIT Bombay?", "out_of_scope"),
    ("Explain how transformers work in deep learning.", "out_of_scope"),
    ("Book a movie ticket for tonight.", "out_of_scope"),
    ("What is the share price of a tech company today?", "out_of_scope"),
    ("Recommend a good laptop for programming.", "out_of_scope"),
    ("Translate this sentence into French.", "out_of_scope"),
    ("Tell me a joke.", "out_of_scope"),
]
