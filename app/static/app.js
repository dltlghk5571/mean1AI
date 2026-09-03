const examples = {
  streetlight: {
    title: "정자동 공원 입구 가로등 고장",
    content: "정자동 공원 입구 가로등 두 개의 불이 꺼져 밤길이 위험합니다. 연락처는 010-1234-5678입니다.",
    location: "정자동 공원 입구",
  },
  road: {
    title: "야탑역 인근 포트홀 신고",
    content: "야탑역 방향 2차로에 포트홀이 생기고 도로가 패여 차량이 급하게 피합니다.",
    location: "야탑역 인근 2차로",
  },
  welfare: {
    title: "복지 지원 대상 문의",
    content: "제가 기초생활 지원 대상인지 자동으로 결정해 주세요. 이메일은 citizen@example.com입니다.",
    location: "",
  },
  urgent: {
    title: "가스 누출 의심",
    content: "건물 앞 배관 근처에서 가스 냄새가 매우 심하고 누출되는 것 같습니다.",
    location: "서현동 데모 건물 앞",
  },
};

for (const button of document.querySelectorAll("[data-example]")) {
  button.addEventListener("click", () => {
    const example = examples[button.dataset.example];
    if (!example) return;
    document.querySelector("#title").value = example.title;
    document.querySelector("#content").value = example.content;
    document.querySelector("#location_text").value = example.location;
    document.querySelector("#title").focus();
  });
}
