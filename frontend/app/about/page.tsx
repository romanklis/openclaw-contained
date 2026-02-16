'use client'

const LINKEDIN_URL = 'https://www.linkedin.com/in/roman-pawel-klis-3811994/'

const expertise = [
  {
    title: 'AI Program Leadership & Strategic Transformation',
    items: [
      'Delivery of enterprise-scale AI initiatives and digital transformation, aligning business roadmaps with technical execution and senior stakeholder management.',
      'Led multi-site AI teams (Switzerland–India) using Lean AI, Agile/Scrum, and corporate engineering strategies to facilitate tech transfer from academia to industry.',
      'Co-supervised Master\'s research at ETH Zurich & EPFL; mentored the winning NASA 2025 Zurich Hackathon team.',
    ],
  },
  {
    title: 'Generative AI & Advanced Machine Learning',
    items: [
      'End-to-end development of Agentic AI, LLM fine-tuning (RAG, Agentic RAG), Generative AI (GenAI), GANs, NLP, and Computer Vision.',
      'Predictive maintenance, anomaly detection, time-series analysis, signal processing, and Kalman filters.',
      'Python (PyTorch, TensorFlow, Hugging Face, LangChain), MATLAB, R.',
    ],
  },
  {
    title: 'Data Architecture & Enterprise Engineering',
    items: [
      'Design of secure, high-availability data pipelines and analytical frameworks (ETL/ELT) across factory, enterprise, and IoT environments.',
      'High-throughput ingestion and retrieval systems using Spark, Hadoop, and Distributed Computing.',
      'IoT & Sensor Fusion and Structural Health Monitoring (SHM) for civil infrastructure.',
      'Java, Scala, C/C++, Embedded C, SQL (MsSQL, PostgreSQL), NoSQL.',
    ],
  },
  {
    title: 'Governance, Compliance & Cloud Operations',
    items: [
      'Operated within ISO-compliant data regimes; expertise in data governance, traceability, and quality control.',
      'Deployment and orchestration across Azure and Google Cloud Platform (GCP) using Docker, Kubernetes/OpenShift, and Jenkins.',
      'Advanced reporting and business intelligence via PowerBI and Tableau.',
    ],
  },
]

const experience = [
  {
    company: 'Johnson Electric',
    location: 'Murten, Switzerland',
    period: 'November 2020 – October 2025',
    role: 'Senior Data Scientist — Corporate Engineering / Senior Manager',
    highlights: [
      'Led end-to-end data architecture efforts across manufacturing lines with ~10s cycle time, delivering scalable, high-throughput pipelines for monitoring and traceability of production defects.',
      'Architected secure data products for business-critical decisions, ensuring efficient retrieval, storage, and access under governance protocols.',
      'Developed Virtual Testing frameworks using time series analysis, reducing EOL testing time by 60%.',
      'Spearheaded AI/ML initiatives including anomaly detection, energy optimization, and sensor signal interpretation.',
      'Built and led offshore data science team in Chennai; mentored cross-functional engineers using Lean methodology.',
      'Developed internal LLMs leveraging company data sources and Azure models; fine-tuned RAG and Agentic RAG pipelines for domain-specific knowledge retrieval.',
      'Drove collaborations with ETH Zurich and EPFL, co-supervising Master\'s research in applied AI and signal processing.',
    ],
  },
  {
    company: 'Philip Morris International',
    location: 'Lausanne, Switzerland',
    period: 'November 2018 – November 2020',
    role: 'CX Product Owner + Data Engineer',
    highlights: [
      'Architected database and data processing backend for NPS analytics system supporting 20+ markets, integrating language feedback into a single AI-driven analytical pipeline.',
      'Solution delivered using OpenShift, used by 30+ internal analysts worldwide with automatic 6-hour update cycles.',
      'Designed ML-optimized analytical views using Spark, simplifying modeling workflows and improving dashboard responsiveness.',
      'Led development of PowerBI tools ingesting multi-source survey and audit data for executive reporting.',
    ],
  },
  {
    company: 'Philip Morris International',
    location: 'Kraków, Poland / Lausanne, Switzerland',
    period: 'November 2016 – November 2018',
    role: 'Enterprise Data Scientist',
    highlights: [
      'Designed predictive maintenance tools utilizing high-volume sensor data streams.',
      'Architected multi-layer data ingestion and processing platforms across multiple manufacturing sites.',
      'Led manufacturing data layout standardization, enabling unified analytics and system integration.',
      'Operated within data provided under a high-security, access-controlled governance regime, aligned with FDA IQOS preparation.',
    ],
  },
  {
    company: 'ETH Zurich',
    location: 'Zurich, Switzerland',
    period: 'January 2011 – September 2015',
    role: 'Research Assistant',
    highlights: [
      'Developed algorithms for vibration monitoring using compressed sensing for efficient Structural Health Monitoring.',
      'Published in leading journals; presented at international conferences.',
      'Contributed to academic-industrial tech transfer and collaborative research.',
    ],
  },
  {
    company: 'NeoStrain Sp. z o.o.',
    location: 'Kraków, Poland',
    period: 'October 2007 – June 2009',
    role: 'Software Developer, Design Department',
    highlights: [
      'Led software architecture for Structural Health Monitoring (SHM) system deployed on 600m Puławy Bridge.',
      'Designed layered, contractually compliant data acquisition, synchronization, and replication system.',
      'Developed orchestration tools and monitoring interfaces for multi-modal sensor integration.',
    ],
  },
]

const education = [
  {
    degree: 'Doctor of Sciences (Dr. sc. ETH Zurich)',
    field: 'Chair of Structural Mechanics and Monitoring',
    institution: 'ETH Zurich, Switzerland',
    period: '2011 – 2016',
  },
  {
    degree: 'Master of Science (M.Sc.)',
    field: 'Mechanical Engineering, Robotics & Mechatronics',
    institution: 'AGH University of Science and Technology, Kraków, Poland',
    period: '2003 – 2008',
  },
]

export default function AboutPage() {
  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">About the Author</h1>
        <p className="text-sm text-gray-500 mt-1">The person behind TaskForge</p>
      </div>

      {/* Profile Card */}
      <div className="bg-[#0f0f1a] border border-[#1a1a2e] rounded-xl p-6">
        <div className="flex flex-col sm:flex-row items-start gap-5">
          {/* Avatar */}
          <div className="w-20 h-20 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-2xl font-bold shrink-0">
            RK
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-xl font-bold text-white">Roman Pawel Klis</h2>
            <p className="text-sm text-indigo-400 mt-0.5">Dr. sc. ETH Zurich</p>
            <p className="text-sm text-gray-400 mt-1">
              Senior Data Scientist (Corporate Engineering) · AI Program Leader · Senior Manager
            </p>
            <p className="text-sm text-gray-500 mt-3 leading-relaxed">
              With 12+ years of experience delivering secure, enterprise-scale AI solutions in manufacturing and R&D.
              Expert in Agentic AI, Generative AI, and complex data architectures under strict governance (FDA-preparatory, ISO).
              Recognized for strategic leadership and mentorship — most recently demonstrated by guiding the
              1st Prize winning team at the NASA Space Apps Challenge 2025 (Zurich).
            </p>
            <div className="mt-4 flex gap-3">
              <a
                href={LINKEDIN_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 bg-[#0077b5] hover:bg-[#006097] text-white text-sm font-medium rounded-lg transition-colors"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
                </svg>
                Connect on LinkedIn
              </a>
            </div>
          </div>
        </div>
      </div>

      {/* Expertise */}
      <div>
        <h3 className="text-lg font-semibold text-white mb-4">Core Expertise</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {expertise.map((area) => (
            <div key={area.title} className="bg-[#0f0f1a] border border-[#1a1a2e] rounded-xl p-5">
              <h4 className="text-sm font-semibold text-indigo-400 mb-3">{area.title}</h4>
              <ul className="space-y-2">
                {area.items.map((item, i) => (
                  <li key={i} className="text-xs text-gray-400 leading-relaxed flex gap-2">
                    <span className="text-indigo-500 mt-1 shrink-0">›</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      {/* Experience */}
      <div>
        <h3 className="text-lg font-semibold text-white mb-4">Professional Experience</h3>
        <div className="space-y-4">
          {experience.map((exp) => (
            <div key={exp.company + exp.period} className="bg-[#0f0f1a] border border-[#1a1a2e] rounded-xl p-5">
              <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-1 mb-3">
                <div>
                  <h4 className="text-sm font-semibold text-white">{exp.company}</h4>
                  <p className="text-xs text-indigo-400">{exp.role}</p>
                </div>
                <div className="text-right shrink-0">
                  <p className="text-xs text-gray-500">{exp.period}</p>
                  <p className="text-xs text-gray-600">{exp.location}</p>
                </div>
              </div>
              <ul className="space-y-1.5">
                {exp.highlights.map((h, i) => (
                  <li key={i} className="text-xs text-gray-400 leading-relaxed flex gap-2">
                    <span className="text-gray-600 mt-0.5 shrink-0">•</span>
                    <span>{h}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      {/* Education */}
      <div>
        <h3 className="text-lg font-semibold text-white mb-4">Education</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {education.map((edu) => (
            <div key={edu.degree} className="bg-[#0f0f1a] border border-[#1a1a2e] rounded-xl p-5">
              <h4 className="text-sm font-semibold text-white">{edu.degree}</h4>
              <p className="text-xs text-indigo-400 mt-1">{edu.field}</p>
              <p className="text-xs text-gray-500 mt-1">{edu.institution}</p>
              <p className="text-xs text-gray-600 mt-0.5">{edu.period}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Recognition */}
      <div>
        <h3 className="text-lg font-semibold text-white mb-4">Recognition</h3>
        <div className="bg-[#0f0f1a] border border-[#1a1a2e] rounded-xl p-5">
          <div className="flex items-start gap-3">
            <span className="text-2xl">🏆</span>
            <div>
              <h4 className="text-sm font-semibold text-white">NASA Space Apps Challenge 2025 — Zurich</h4>
              <p className="text-xs text-gray-400 mt-1">Mentored the 1st Prize winning team and the Audience Award team.</p>
            </div>
          </div>
        </div>
      </div>

      {/* CTA */}
      <div className="bg-gradient-to-r from-indigo-500/10 to-purple-500/10 border border-indigo-500/20 rounded-xl p-6 text-center">
        <p className="text-sm text-gray-400 mb-3">If you&apos;d like to discuss how TaskForge could be used in your organization — feel free to reach out.</p>
        <a
          href={LINKEDIN_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-[#0077b5] hover:bg-[#006097] text-white text-sm font-medium rounded-lg transition-colors"
        >
          <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
            <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
          </svg>
          Reach out on LinkedIn
        </a>
      </div>
    </div>
  )
}
